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

/// Intentos de arranque antes de darse por vencido.
///
/// El almacenamiento admite un solo proceso a la vez, y el bloqueo lo suelta el
/// sistema operativo al terminar el que lo tiene, no de inmediato. Si la app
/// anterior murió de golpe —un cierre forzado, un corte— su Qdrant puede seguir
/// vivo unos segundos: reintentar lo cubre, fallar al primer intento dejaría la
/// app inservible hasta reiniciar la sesión.
const LOCK_RETRIES: u32 = 5;

/// Espera entre reintentos.
const LOCK_RETRY_DELAY: Duration = Duration::from_secs(2);

/// Lanza Qdrant sobre el almacenamiento del directorio de datos del usuario.
pub fn spawn(exe: &Path, data_dir: &Path) -> Result<Service, String> {
    let storage = data_dir.join("qdrant").join("storage");
    let snapshots = data_dir.join("qdrant").join("snapshots");
    std::fs::create_dir_all(&storage)
        .map_err(|e| format!("no se pudo crear {}: {e}", storage.display()))?;

    let mut last_error = String::new();

    for attempt in 1..=LOCK_RETRIES {
        let storage = storage.clone();
        let snapshots = snapshots.clone();

        match Service::spawn("qdrant", exe, "/healthz", STARTUP_TIMEOUT, move |command, port| {
            command
                // Qdrant acepta toda su configuración por entorno, con doble
                // guion bajo para anidar. Así no hace falta escribirle un
                // archivo.
                .env("QDRANT__SERVICE__HTTP_PORT", port.to_string())
                .env("QDRANT__STORAGE__STORAGE_PATH", &storage)
                .env("QDRANT__STORAGE__SNAPSHOTS_PATH", &snapshots)
                // Solo escucha en localhost: el vector store no tiene
                // autenticación y no debe quedar expuesto a la red.
                .env("QDRANT__SERVICE__HOST", "127.0.0.1")
                .env("QDRANT__SERVICE__ENABLE_TLS", "false")
                .env("QDRANT__TELEMETRY_DISABLED", "true");
        }) {
            Ok(service) => return Ok(service),
            Err(error) => {
                last_error = error;
                if attempt < LOCK_RETRIES {
                    log::warn!(
                        "Qdrant no arrancó (intento {attempt} de {LOCK_RETRIES}); \
                         puede quedar una instancia anterior cerrándose"
                    );
                    std::thread::sleep(LOCK_RETRY_DELAY);
                }
            }
        }
    }

    Err(format!(
        "{last_error}\n\nSi el problema persiste, puede haber quedado otra \
         instancia de Mnemosyne abierta: cerrala y volvé a intentar."
    ))
}

/// Ubica el ejecutable de Qdrant incluido en el paquete.
///
/// En la app instalada viaja como recurso junto al binario. En desarrollo ese
/// recurso no existe todavía, así que se cae a la copia que
/// `npm run fetch:qdrant` deja en `desktop/binaries/`.
pub fn resolve_executable(app: &tauri::AppHandle) -> Option<PathBuf> {
    crate::paths::resolve_bundled(app, "qdrant", EXECUTABLE)
}
