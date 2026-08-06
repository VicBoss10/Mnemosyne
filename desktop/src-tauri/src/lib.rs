//! Mnemosyne para escritorio.
//!
//! Esta capa no contiene lógica del motor RAG: arranca la API de Python como
//! proceso hijo, espera a que esté lista, y apunta la ventana a su interfaz web.
//! Todo lo que se ve en pantalla lo sirve la misma API que `mnemosyne serve`.

mod backend;

use std::path::PathBuf;
use std::sync::Arc;

use tauri::{Manager, WindowEvent};

use backend::Backend;

/// Nombre del ejecutable de la API empaquetado junto a la app.
#[cfg(windows)]
const API_EXECUTABLE: &str = "mnemosyne-api.exe";
#[cfg(not(windows))]
const API_EXECUTABLE: &str = "mnemosyne-api";

/// Estado compartido de la app: el proceso de la API.
struct AppState {
    backend: Arc<Backend>,
}

/// Devuelve la URL base de la API, para que la interfaz sepa a dónde hablar.
#[tauri::command]
fn api_base_url(state: tauri::State<AppState>) -> String {
    state.backend.base_url.clone()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
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
            let data_dir = app
                .path()
                .app_data_dir()
                .map_err(|e| format!("no se pudo resolver el directorio de datos: {e}"))?;
            std::fs::create_dir_all(&data_dir)
                .map_err(|e| format!("no se pudo crear {}: {e}", data_dir.display()))?;

            let exe = resolve_api_executable(app.handle())?;
            log::info!("lanzando API desde {}", exe.display());

            let backend = Arc::new(Backend::spawn(&exe, &data_dir)?);
            log::info!("API disponible en {}", backend.base_url);

            // La ventana se crea acá y no en tauri.conf.json —que la declara
            // vacía a propósito— por dos razones: su URL es el puerto que la API
            // tomó al arrancar, desconocido hasta este punto, y crearla recién
            // ahora evita mostrar un error de conexión mientras Python levanta.
            let url = tauri::WebviewUrl::External(
                backend
                    .base_url
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
                backend: backend.clone(),
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // Cerrar la ventana debe matar la API: de lo contrario queda un
            // proceso huérfano reteniendo el puerto y el índice.
            if matches!(event, WindowEvent::Destroyed) {
                if let Some(state) = window.try_state::<AppState>() {
                    state.backend.shutdown();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![api_base_url])
        .run(tauri::generate_context!())
        .expect("error al ejecutar la aplicación");
}

/// Ubica el ejecutable de la API.
///
/// En la app instalada viaja como recurso junto al binario. En desarrollo ese
/// recurso no existe todavía, así que se cae a la copia que PyInstaller deja en
/// `desktop/binaries/`, y de ahí al `mnemosyne` del entorno virtual del repo.
fn resolve_api_executable(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let mut candidates: Vec<PathBuf> = Vec::new();

    // PyInstaller en modo --onedir anida el ejecutable en un subdirectorio con
    // el nombre del programa, junto a sus librerías.
    if let Ok(dir) = app.path().resource_dir() {
        candidates.push(dir.join("mnemosyne-api").join(API_EXECUTABLE));
        candidates.push(dir.join(API_EXECUTABLE));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join(API_EXECUTABLE));
        }
    }

    // Desarrollo: el binario que deja `python desktop/build_api.py`.
    let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .map(PathBuf::from);
    if let Some(root) = repo_root {
        candidates.push(
            root.join("desktop")
                .join("binaries")
                .join("mnemosyne-api")
                .join(API_EXECUTABLE),
        );
    }

    candidates
        .iter()
        .find(|p| p.is_file())
        .cloned()
        .ok_or_else(|| {
            format!(
                "no se encontró el ejecutable de la API. Buscado en:\n{}",
                candidates
                    .iter()
                    .map(|p| format!("  - {}", p.display()))
                    .collect::<Vec<_>>()
                    .join("\n")
            )
        })
}
