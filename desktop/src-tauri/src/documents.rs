//! Elección de la carpeta de documentos desde la app.
//!
//! Es la diferencia práctica entre la app y la versión web: en vez de editar
//! `docs_path` en el YAML y reiniciar, se elige la carpeta con el selector del
//! sistema y se re-indexa en el momento.
//!
//! El motor no cambia para esto: `POST /ingest` ya acepta la ruta a indexar, y
//! la ruta elegida se guarda junto al resto de los datos de la app para que el
//! siguiente arranque la recuerde.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};

/// La ingesta re-lee y vuelve a embeber todo el corpus, así que su duración
/// crece con el número de documentos.
const INGEST_TIMEOUT: Duration = Duration::from_secs(3600);

/// Nombre del archivo donde se recuerda la carpeta elegida.
const STATE_FILE: &str = "desktop-state.json";

/// Lo que la app recuerda entre arranques.
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct State {
    /// Carpeta de documentos elegida por el usuario, si eligió alguna.
    pub docs_path: Option<PathBuf>,
}

impl State {
    pub fn load(data_dir: &Path) -> Self {
        let path = data_dir.join(STATE_FILE);
        std::fs::read_to_string(path)
            .ok()
            .and_then(|raw| serde_json::from_str(&raw).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, data_dir: &Path) -> Result<(), String> {
        let path = data_dir.join(STATE_FILE);
        let raw = serde_json::to_string_pretty(self)
            .map_err(|e| format!("no se pudo serializar el estado: {e}"))?;
        std::fs::write(&path, raw)
            .map_err(|e| format!("no se pudo escribir {}: {e}", path.display()))
    }
}

/// Resultado de una re-indexación, tal como lo devuelve la API.
#[derive(Debug, Serialize, Deserialize)]
pub struct IngestResult {
    pub documents: u32,
    pub chunks: u32,
    pub collection: String,
}

/// Indexa una carpeta llamando a la API, que es la que sabe hacerlo.
pub fn ingest(api_base_url: &str, folder: &Path) -> Result<IngestResult, String> {
    let response = ureq::post(&format!("{api_base_url}/ingest"))
        .timeout(INGEST_TIMEOUT)
        .send_json(ureq::json!({ "path": folder.to_string_lossy() }))
        .map_err(|e| match e {
            // La API explica en el cuerpo qué salió mal —carpeta inexistente,
            // sin documentos legibles—, y ese detalle es lo único accionable.
            ureq::Error::Status(_, response) => response
                .into_string()
                .ok()
                .and_then(|body| {
                    serde_json::from_str::<serde_json::Value>(&body)
                        .ok()
                        .and_then(|v| v.get("detail")?.as_str().map(String::from))
                })
                .unwrap_or_else(|| "la indexación falló".to_string()),
            other => format!("no se pudo contactar la API: {other}"),
        })?;

    response
        .into_json()
        .map_err(|e| format!("respuesta inesperada de la API: {e}"))
}
