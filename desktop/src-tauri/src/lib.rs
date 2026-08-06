//! Mnemosyne para escritorio.
//!
//! Esta capa no contiene lógica del motor RAG: levanta el vector store y la API
//! de Python como procesos hijos, los espera hasta que responden, y apunta la
//! ventana a la interfaz que la propia API sirve. Todo lo que se ve en pantalla
//! lo genera el mismo código que `mnemosyne serve`.

mod api;
mod config;
mod documents;
mod ollama;
mod paths;
mod qdrant;
mod service;

use std::path::PathBuf;
use std::sync::Arc;

use tauri::{Manager, WindowEvent};
use tauri_plugin_dialog::DialogExt;

use service::Service;

/// Completa `window.__TAURI__` cuando el webview solo expone los primitivos.
///
/// `withGlobalTauri` inyecta el objeto envuelto en las páginas del propio
/// bundle, pero la interfaz de Mnemosyne la sirve la API por HTTP: es un origen
/// remoto y allí llegan los primitivos (`__TAURI_INTERNALS__`) sin la envoltura
/// que la página espera. Construirla es cuestión de dos funciones.
///
/// Si el objeto ya está completo no se toca, para no pisar la implementación
/// oficial cuando sí la haya.
const GLOBAL_TAURI_SHIM: &str = r#"
(function () {
  if (window.__TAURI__ && window.__TAURI__.core) return;
  const internals = window.__TAURI_INTERNALS__;
  if (!internals) return;

  const invoke = (cmd, args, options) => internals.invoke(cmd, args, options);

  window.__TAURI__ = Object.assign({}, window.__TAURI__, {
    core: { invoke },
    opener: {
      openUrl: (url) => invoke('plugin:opener|open_url', { url }),
    },
  });
})();
"#;

/// Los procesos que la app supervisa, en el orden en que deben apagarse.
struct AppState {
    api: Arc<Service>,
    qdrant: Arc<Service>,
    data_dir: PathBuf,
}

impl AppState {
    /// Detiene los servicios. La API primero, para que no quede consultando un
    /// vector store que ya se fue.
    fn shutdown(&self) {
        self.api.shutdown();
        self.qdrant.shutdown();
    }
}

/// Devuelve la URL base de la API, para que la interfaz sepa a dónde hablar.
#[tauri::command]
fn api_base_url(state: tauri::State<AppState>) -> String {
    state.api.base_url.clone()
}

/// Carpeta de documentos activa, o `null` si todavía no se eligió ninguna.
#[tauri::command]
fn docs_folder(state: tauri::State<AppState>) -> Option<String> {
    documents::State::load(&state.data_dir)
        .docs_path
        .map(|p| p.to_string_lossy().into_owned())
}

/// Abre el selector del sistema, indexa la carpeta elegida y la recuerda.
///
/// Devuelve `null` si el usuario cancela, para que la interfaz distinga
/// "canceló" de "falló".
#[tauri::command]
async fn choose_docs_folder(
    app: tauri::AppHandle,
    state: tauri::State<'_, AppState>,
) -> Result<Option<documents::IngestResult>, String> {
    let Some(folder) = app.dialog().file().blocking_pick_folder() else {
        return Ok(None);
    };
    let folder = folder
        .into_path()
        .map_err(|e| format!("ruta no válida: {e}"))?;

    let result = documents::ingest(&state.api.base_url, &folder)?;

    documents::State {
        docs_path: Some(folder.clone()),
    }
    .save(&state.data_dir)?;

    log::info!(
        "indexados {} documentos ({} fragmentos) desde {}",
        result.documents,
        result.chunks,
        folder.display()
    );
    Ok(Some(result))
}

/// Re-indexa la carpeta ya elegida, para recoger cambios en los archivos.
#[tauri::command]
async fn reindex(state: tauri::State<'_, AppState>) -> Result<documents::IngestResult, String> {
    let folder = documents::State::load(&state.data_dir)
        .docs_path
        .ok_or_else(|| "todavía no elegiste una carpeta de documentos".to_string())?;

    documents::ingest(&state.api.base_url, &folder)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(
            tauri_plugin_log::Builder::new()
                .target(tauri_plugin_log::Target::new(
                    tauri_plugin_log::TargetKind::Stdout,
                ))
                .target(tauri_plugin_log::Target::new(
                    tauri_plugin_log::TargetKind::LogDir { file_name: None },
                ))
                .level(log::LevelFilter::Info)
                .build(),
        )
        .setup(|app| {
            let handle = app.handle();

            let data_dir = app
                .path()
                .app_data_dir()
                .map_err(|e| format!("no se pudo resolver el directorio de datos: {e}"))?;
            std::fs::create_dir_all(&data_dir)
                .map_err(|e| format!("no se pudo crear {}: {e}", data_dir.display()))?;

            // El vector store primero: la API se conecta a él al arrancar.
            let qdrant_exe = qdrant::resolve_executable(handle).ok_or_else(|| {
                format!(
                    "no se encontró el ejecutable de Qdrant. Buscado en:\n{}",
                    paths::describe(&paths::candidates(handle, "qdrant", qdrant::EXECUTABLE))
                )
            })?;
            log::info!("lanzando Qdrant desde {}", qdrant_exe.display());
            let qdrant = Arc::new(qdrant::spawn(&qdrant_exe, &data_dir)?);

            // Ollama no se empaqueta —pesa más que todo el resto junto— así que
            // puede faltar. No es motivo para abortar: la ventana se abre igual
            // y la interfaz guía la instalación.
            let ollama = ollama::status(&config::required_models(&data_dir));
            if ollama.ready() {
                log::info!("Ollama listo con todos los modelos");
            } else if ollama.running {
                log::warn!("faltan modelos de Ollama: {:?}", ollama.missing_models);
            } else {
                log::warn!("Ollama no responde en {}", ollama::BASE_URL);
            }

            let api_exe = api::resolve_executable(handle).ok_or_else(|| {
                format!(
                    "no se encontró el ejecutable de la API. Buscado en:\n{}",
                    paths::describe(&paths::candidates(handle, api::BUNDLE_DIR, api::EXECUTABLE))
                )
            })?;
            log::info!("lanzando la API desde {}", api_exe.display());
            let api = Arc::new(api::spawn(&api_exe, &data_dir, &qdrant.base_url)?);

            // La ventana se crea acá y no en tauri.conf.json —que declara la
            // lista vacía a propósito— por dos razones: su URL es el puerto que
            // la API tomó al arrancar, desconocido hasta este punto, y crearla
            // recién ahora evita mostrar un error de conexión mientras los
            // servicios levantan.
            // Por nombre de host y no por IP: el patrón de orígenes remotos con
            // acceso a comandos no reconoce direcciones IP
            // (tauri-apps/tauri#7009), y sin eso la ventana no puede invocar
            // nada de la capa nativa.
            let url = tauri::WebviewUrl::External(
                api.local_url
                    .parse()
                    .map_err(|e| format!("URL inválida: {e}"))?,
            );

            tauri::WebviewWindowBuilder::new(app, "main", url)
                .title("Mnemosyne")
                .inner_size(1100.0, 780.0)
                .min_inner_size(640.0, 480.0)
                .center()
                // La interfaz llega por HTTP desde la API, así que para el
                // webview su origen es remoto y `withGlobalTauri` no le inyecta
                // window.__TAURI__. Sin esto la página no puede invocar ningún
                // comando: se queda con los controles nativos muertos y el
                // asistente sin poder consultar si falta algo.
                .initialization_script(GLOBAL_TAURI_SHIM)
                .build()
                .map_err(|e| format!("no se pudo crear la ventana: {e}"))?;

            app.manage(AppState {
                api,
                qdrant,
                data_dir,
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // Cerrar la ventana debe matar a los hijos: de lo contrario quedan
            // procesos huérfanos reteniendo sus puertos y el almacenamiento.
            if matches!(event, WindowEvent::Destroyed) {
                if let Some(state) = window.try_state::<AppState>() {
                    state.shutdown();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            api_base_url,
            docs_folder,
            choose_docs_folder,
            reindex
        ])
        .run(tauri::generate_context!())
        .expect("error al ejecutar la aplicación");
}
