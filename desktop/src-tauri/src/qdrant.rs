//! Arranque del vector store.
//!
//! Qdrant viaja dentro del instalador como un binario suelto y la app lo lanza
//! como proceso hijo. Eso es lo que quita a Docker de los requisitos: el motor
//! sigue hablando con un Qdrant real por HTTP —`core/store.py` no cambia— pero
//! quien lo levanta es la app y no `docker compose`.
//!
//! No se usa el modo embebido de qdrant-client, que evitaría el proceso
//! aparte: obligaría a tocar el motor, y el despliegue con Docker de la fase 2
//! necesita el servicio real de todos modos.

use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::service::Service;

/// Nombre del ejecutable dentro del paquete.
#[cfg(windows)]
pub const EXECUTABLE: &str = "qdrant.exe";
#[cfg(not(windows))]
pub const EXECUTABLE: &str = "qdrant";

/// Plazo de arranque. Qdrant abre su índice en memoria al iniciar, así que
/// crece con el tamaño del corpus.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);

/// Lanza Qdrant sobre el almacenamiento del directorio de datos del usuario.
pub fn spawn(exe: &Path, data_dir: &Path) -> Result<Service, String> {
    let storage = data_dir.join("qdrant").join("storage");
    let snapshots = data_dir.join("qdrant").join("snapshots");
    std::fs::create_dir_all(&storage)
        .map_err(|e| format!("no se pudo crear {}: {e}", storage.display()))?;

    Service::spawn("qdrant", exe, "/healthz", STARTUP_TIMEOUT, |command, port| {
        command
            // Qdrant acepta toda su configuración por entorno, con doble guion
            // bajo para anidar. Así no hace falta escribirle un archivo.
            .env("QDRANT__SERVICE__HTTP_PORT", port.to_string())
            .env("QDRANT__STORAGE__STORAGE_PATH", &storage)
            .env("QDRANT__STORAGE__SNAPSHOTS_PATH", &snapshots)
            // Solo escucha en localhost: el vector store no tiene autenticación
            // y no debe quedar expuesto a la red.
            .env("QDRANT__SERVICE__HOST", "127.0.0.1")
            // El puerto gRPC se desactiva: el motor usa la API HTTP, y dejarlo
            // en su valor por defecto haría chocar dos instancias de la app.
            .env("QDRANT__SERVICE__ENABLE_TLS", "false")
            .env("QDRANT__TELEMETRY_DISABLED", "true");
    })
}

/// Ubica el ejecutable de Qdrant incluido en el paquete.
///
/// En la app instalada viaja como recurso junto al binario. En desarrollo ese
/// recurso no existe todavía, así que se cae a la copia que
/// `npm run fetch:qdrant` deja en `desktop/binaries/`.
pub fn resolve_executable(app: &tauri::AppHandle) -> Option<PathBuf> {
    crate::paths::resolve_bundled(app, "qdrant", EXECUTABLE)
}
