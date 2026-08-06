//! Detección de Ollama y descarga de los modelos.
//!
//! Ollama es la única dependencia que no viaja dentro del instalador: pesa
//! ~1,4 GB en Windows y Linux por las librerías de CUDA, y los modelos suman
//! varios gigabytes más. Empaquetarlo daría un instalador de más de 5 GB del
//! que la mayor parte sobra en las máquinas donde Ollama ya está.
//!
//! En su lugar la app comprueba qué falta y lo instala guiada por el usuario.
//! Nada se descarga sin que lo pida explícitamente: son gigabytes y software de
//! terceros, así que la decisión es suya y el origen —las releases oficiales del
//! proyecto— se muestra antes de empezar.

use std::io::{BufRead, BufReader};
use std::time::Duration;

use serde::{Deserialize, Serialize};

/// Dónde escucha Ollama. Es su puerto fijo por convención: a diferencia de los
/// servicios que la app lanza, este proceso es del sistema y no lo elegimos.
pub const BASE_URL: &str = "http://127.0.0.1:11434";

/// Sondeo de presencia. Corto: solo distingue "responde" de "no está".
const PROBE_TIMEOUT: Duration = Duration::from_secs(3);

/// Estado de las dependencias que la app necesita para responder.
#[derive(Debug, Serialize)]
pub struct Status {
    /// Si el servicio responde en su puerto.
    pub running: bool,
    /// Modelos que faltan, de los configurados en el YAML del usuario.
    pub missing_models: Vec<String>,
}

impl Status {
    /// Si la app puede responder preguntas tal como está.
    pub fn ready(&self) -> bool {
        self.running && self.missing_models.is_empty()
    }
}

/// Modelo de la lista que devuelve Ollama.
#[derive(Debug, Deserialize)]
struct Model {
    name: String,
}

#[derive(Debug, Deserialize)]
struct TagsResponse {
    #[serde(default)]
    models: Vec<Model>,
}

/// Comprueba si Ollama responde y qué modelos le faltan.
///
/// `required` son los que pide la configuración activa; se consultan a la API en
/// vez de fijarlos acá porque el usuario puede cambiarlos en su `config.yaml`.
pub fn status(required: &[String]) -> Status {
    let Ok(response) = ureq::get(&format!("{BASE_URL}/api/tags"))
        .timeout(PROBE_TIMEOUT)
        .call()
    else {
        return Status {
            running: false,
            // Sin servicio no se puede saber qué hay descargado; se asume que
            // falta todo, que es lo que el asistente tendrá que resolver.
            missing_models: required.to_vec(),
        };
    };

    let installed: Vec<String> = response
        .into_json::<TagsResponse>()
        .map(|tags| tags.models.into_iter().map(|m| m.name).collect())
        .unwrap_or_default();

    Status {
        running: true,
        missing_models: required
            .iter()
            .filter(|needed| !is_installed(needed, &installed))
            .cloned()
            .collect(),
    }
}

/// Si un modelo requerido está entre los instalados.
///
/// Ollama normaliza los nombres sin etiqueta añadiéndoles `:latest`, así que
/// `bge-m3` y `bge-m3:latest` son el mismo modelo y hay que compararlos como
/// tales.
fn is_installed(required: &str, installed: &[String]) -> bool {
    let canonical = |name: &str| {
        if name.contains(':') {
            name.to_string()
        } else {
            format!("{name}:latest")
        }
    };
    let target = canonical(required);
    installed.iter().any(|name| canonical(name) == target)
}

/// Avance de una descarga, tal como se le informa a la interfaz.
#[derive(Debug, Clone, Serialize)]
pub struct PullProgress {
    /// Modelo que se está descargando.
    pub model: String,
    /// Fase informada por Ollama ("pulling manifest", "verifying sha256", …).
    pub status: String,
    /// Bytes descargados y totales, cuando la fase los reporta.
    pub completed: u64,
    pub total: u64,
}

/// Línea del flujo NDJSON que devuelve `/api/pull`.
#[derive(Debug, Deserialize)]
struct PullLine {
    status: String,
    #[serde(default)]
    completed: u64,
    #[serde(default)]
    total: u64,
    #[serde(default)]
    error: Option<String>,
}

/// Descarga un modelo, informando el avance por cada línea de progreso.
///
/// La descarga puede durar varios minutos y son gigabytes, así que no se espera
/// en silencio: `on_progress` recibe cada actualización para que la interfaz
/// muestre una barra real en vez de un spinner indefinido.
pub fn pull<F>(model: &str, mut on_progress: F) -> Result<(), String>
where
    F: FnMut(PullProgress),
{
    // Timeout de lectura amplio y no el de por defecto: entre dos líneas de
    // progreso puede pasar un rato largo —verificar un blob de gigabytes no
    // emite nada— y cortar ahí abortaría una descarga que va bien.
    let agent = ureq::AgentBuilder::new()
        .timeout_read(Duration::from_secs(600))
        .build();

    let response = agent
        .post(&format!("{BASE_URL}/api/pull"))
        .send_json(ureq::json!({ "model": model, "stream": true }))
        .map_err(|e| format!("no se pudo iniciar la descarga de {model}: {e}"))?;

    for line in BufReader::new(response.into_reader()).lines() {
        let line = line.map_err(|e| format!("se cortó la descarga de {model}: {e}"))?;
        if line.trim().is_empty() {
            continue;
        }

        let parsed: PullLine = serde_json::from_str(&line)
            .map_err(|e| format!("respuesta inesperada de Ollama: {e}"))?;

        if let Some(error) = parsed.error {
            return Err(format!("Ollama rechazó la descarga de {model}: {error}"));
        }

        on_progress(PullProgress {
            model: model.to_string(),
            status: parsed.status,
            completed: parsed.completed,
            total: parsed.total,
        });
    }

    Ok(())
}
