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

/// Deja el binario listo para ejecutar y devuelve su ruta.
///
/// En Linux viaja comprimido y se descomprime en el directorio de datos del
/// usuario la primera vez. El rodeo lo impone el empaquetado del AppImage:
/// linuxdeploy recorre los ELF del paquete y les pasa patchelf para reescribir
/// sus rutas de librerías, pero Qdrant viene enlazado estáticamente
/// (static-pie) y ese parcheo le inyecta un RUNPATH que no debería tener,
/// dejándolo muerto con SIGSEGV antes de emitir una línea de log. Tauri no
/// ofrece forma de excluir un recurso del recorrido (tauri-apps/tauri#11898) y
/// quitarle el permiso de ejecución no basta: lo detecta igual. Comprimido no
/// lo reconoce como ELF.
///
/// En el resto de los sistemas el binario viaja tal cual y esto no hace nada.
#[cfg(target_os = "linux")]
fn prepare(exe: &Path, data_dir: &Path) -> Result<std::path::PathBuf, String> {
    use std::io::Read;
    use std::os::unix::fs::PermissionsExt;

    let is_runnable = |path: &Path| {
        std::fs::metadata(path)
            .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    };

    // Ya está listo para ejecutar: en desarrollo, o en un arranque posterior.
    // No basta con que exista: el empaquetado deja el binario sin permiso de
    // ejecución, y lanzarlo así falla con "permiso denegado".
    if is_runnable(exe) {
        return Ok(exe.to_path_buf());
    }

    let archive = exe.with_extension("gz");
    if !archive.is_file() {
        return Err(format!(
            "no se encontró el ejecutable de Qdrant ni en {} ni en {}",
            exe.display(),
            archive.display()
        ));
    }

    let local = data_dir.join("bin");
    std::fs::create_dir_all(&local)
        .map_err(|e| format!("no se pudo crear {}: {e}", local.display()))?;
    let target = local.join(EXECUTABLE);

    // Ya descomprimido en un arranque anterior.
    if is_runnable(&target) {
        return Ok(target);
    }

    log::info!("descomprimiendo Qdrant en {}", target.display());
    let compressed = std::fs::File::open(&archive)
        .map_err(|e| format!("no se pudo abrir {}: {e}", archive.display()))?;
    let mut decoder = flate2::read::GzDecoder::new(compressed);
    let mut bytes = Vec::new();
    decoder
        .read_to_end(&mut bytes)
        .map_err(|e| format!("no se pudo descomprimir Qdrant: {e}"))?;

    // Se escribe en un temporal y se renombra: si la app muere a mitad, el
    // siguiente arranque no encuentra un binario truncado que parezca válido.
    let partial = target.with_extension("partial");
    std::fs::write(&partial, &bytes)
        .map_err(|e| format!("no se pudo escribir {}: {e}", partial.display()))?;
    std::fs::set_permissions(&partial, std::fs::Permissions::from_mode(0o755))
        .map_err(|e| format!("no se pudo marcar Qdrant como ejecutable: {e}"))?;
    std::fs::rename(&partial, &target)
        .map_err(|e| format!("no se pudo mover Qdrant a su sitio: {e}"))?;

    Ok(target)
}

#[cfg(not(target_os = "linux"))]
fn prepare(exe: &Path, _data_dir: &Path) -> Result<std::path::PathBuf, String> {
    Ok(exe.to_path_buf())
}

/// Lanza Qdrant sobre el almacenamiento del directorio de datos del usuario.
pub fn spawn(exe: &Path, data_dir: &Path) -> Result<Service, String> {
    let exe = prepare(exe, data_dir)?;
    let exe = exe.as_path();

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
                // Un binario roto, incompatible o sin permisos no mejora con el
                // tiempo: reintentar solo demora el error.
                if error.contains("signal:") || error.contains("os error 13") {
                    return Err(format!(
                        "{error}\n\nEl ejecutable de Qdrant no se puede correr en este \
                         sistema. Si venís de compilar el paquete, comprobá que no haya \
                         sido modificado durante el empaquetado."
                    ));
                }

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
