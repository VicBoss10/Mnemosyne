//! Arranque de la API de Python.
//!
//! La app no reimplementa el motor: lanza la misma aplicación FastAPI que
//! `mnemosyne serve` levanta, y carga su interfaz en la ventana. El proceso
//! Python no sabe que corre dentro de una app de escritorio; solo recibe por
//! entorno dónde escuchar y a qué Qdrant hablarle.

use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::service::Service;

/// Nombre del ejecutable dentro del paquete.
#[cfg(windows)]
pub const EXECUTABLE: &str = "mnemosyne-api.exe";
#[cfg(not(windows))]
pub const EXECUTABLE: &str = "mnemosyne-api";

/// Subdirectorio donde PyInstaller deja el ejecutable junto a sus librerías.
pub const BUNDLE_DIR: &str = "mnemosyne-api";

/// Plazo de arranque, generoso porque el primer inicio importa el runtime
/// completo de Python desde disco.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);

/// Lanza la API apuntándola al Qdrant que la app ya levantó.
pub fn spawn(exe: &Path, data_dir: &Path, qdrant_url: &str) -> Result<Service, String> {
    let data_dir = data_dir.to_path_buf();
    let qdrant_url = qdrant_url.to_string();

    Service::spawn("api", exe, "/health", STARTUP_TIMEOUT, move |command, port| {
        command
            .env("MNEMOSYNE_HOST", "127.0.0.1")
            .env("MNEMOSYNE_PORT", port.to_string())
            // El índice y la configuración viven en el directorio de datos del
            // sistema operativo: en Windows el directorio de instalación es de
            // solo lectura para el usuario.
            .env("MNEMOSYNE_DATA_DIR", &data_dir)
            // Qdrant escucha en un puerto pedido libre, así que la dirección no
            // se conoce hasta después de arrancarlo. El motor lee esta variable
            // con precedencia sobre su YAML.
            .env("MNEMOSYNE_QDRANT__URL", &qdrant_url);
    })
}

/// Ubica el ejecutable de la API incluido en el paquete.
pub fn resolve_executable(app: &tauri::AppHandle) -> Option<PathBuf> {
    crate::paths::resolve_bundled(app, BUNDLE_DIR, EXECUTABLE)
}
