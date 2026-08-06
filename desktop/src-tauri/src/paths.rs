//! Ubicación de los binarios que la app lleva empaquetados.
//!
//! La misma búsqueda sirve en dos situaciones distintas: en la app instalada
//! los binarios viven junto al ejecutable, como recursos del paquete; en
//! desarrollo todavía no hay paquete, y están en `desktop/binaries/`, que es
//! donde los dejan los scripts de preparación.

use std::path::PathBuf;

use tauri::Manager;

/// Busca un binario empaquetado y devuelve la primera ruta que exista.
///
/// `dir` es el subdirectorio que lo contiene —PyInstaller y las descargas de
/// binarios dejan cada programa en el suyo— y `exe` el nombre del ejecutable,
/// que ya incluye el sufijo `.exe` en Windows.
pub fn resolve_bundled(app: &tauri::AppHandle, dir: &str, exe: &str) -> Option<PathBuf> {
    candidates(app, dir, exe).into_iter().find(|path| {
        // En Linux, Qdrant viaja comprimido para que el empaquetado del AppImage
        // no lo altere —ver qdrant::prepare—, así que la ruta buena puede ser la
        // del archivo o la de su .gz. Se devuelve siempre la del ejecutable;
        // descomprimirlo es tarea de quien lo lanza.
        path.is_file() || path.with_extension("gz").is_file()
    })
}

/// Todas las rutas donde puede estar el binario, en orden de preferencia.
///
/// Se expone para poder informar en el error qué se buscó y dónde, que es la
/// única pista útil cuando falta un recurso del paquete.
pub fn candidates(app: &tauri::AppHandle, dir: &str, exe: &str) -> Vec<PathBuf> {
    let mut found = Vec::new();

    if let Ok(resources) = app.path().resource_dir() {
        found.push(resources.join(dir).join(exe));
        found.push(resources.join(exe));
    }

    if let Ok(current) = std::env::current_exe() {
        if let Some(parent) = current.parent() {
            found.push(parent.join(dir).join(exe));
            found.push(parent.join(exe));
        }
    }

    // Desarrollo: lo que dejan `npm run build:api` y `npm run fetch:qdrant`.
    let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .map(PathBuf::from);
    if let Some(root) = repo_root {
        found.push(root.join("desktop").join("binaries").join(dir).join(exe));
    }

    found
}

/// Formatea las rutas buscadas para un mensaje de error.
pub fn describe(paths: &[PathBuf]) -> String {
    paths
        .iter()
        .map(|p| format!("  - {}", p.display()))
        .collect::<Vec<_>>()
        .join("\n")
}
