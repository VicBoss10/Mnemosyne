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

use tauri::{Emitter, Manager, WindowEvent};
use tauri_plugin_dialog::DialogExt;

use service::Service;

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

/// Estado de Ollama y de los modelos que pide la configuración activa.
#[tauri::command]
fn ollama_status(state: tauri::State<AppState>) -> ollama::Status {
    ollama::status(&config::required_models(&state.data_dir))
}

/// Descarga los modelos que falten, emitiendo el progreso a la interfaz.
///
/// Se ejecuta fuera del hilo principal —son varios minutos y gigabytes— y
/// publica un evento por cada actualización, para que la ventana muestre una
/// barra real en vez de quedarse congelada.
#[tauri::command]
async fn pull_missing_models(
    app: tauri::AppHandle,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    let missing = ollama::status(&config::required_models(&state.data_dir)).missing_models;

    tauri::async_runtime::spawn_blocking(move || {
        for model in missing {
            log::info!("descargando el modelo {model}");
            let handle = app.clone();
            ollama::pull(&model, |progress| {
                let _ = handle.emit("ollama://progress", progress);
            })?;
        }
        let _ = app.emit("ollama://done", ());
        Ok(())
    })
    .await
    .map_err(|e| format!("la descarga se interrumpió: {e}"))?
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
            let url = tauri::WebviewUrl::External(
                api.base_url
                    .parse()
                    .map_err(|e| format!("URL inválida: {e}"))?,
            );

            tauri::WebviewWindowBuilder::new(app, "main", url)
                .title("Mnemosyne")
                .inner_size(1100.0, 780.0)
                .min_inner_size(640.0, 480.0)
                .center()
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
            reindex,
            ollama_status,
            pull_missing_models
        ])
        .run(tauri::generate_context!())
        .expect("error al ejecutar la aplicación");
}
