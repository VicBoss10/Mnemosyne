//! Detección de Ollama, para dejar constancia en el log al arrancar.
//!
//! Ollama es la única dependencia que no viaja dentro del instalador: pesa
//! ~1,4 GB en Windows y Linux por las librerías de CUDA, y los modelos suman
//! varios gigabytes más. Empaquetarlo daría un instalador de más de 5 GB del
//! que la mayor parte sobra en las máquinas donde Ollama ya está.
//!
//! La comprobación que ve el usuario no pasa por acá: la hace la interfaz
//! contra `/dependencies`, y descarga los modelos con `/dependencies/pull`. Está
//! del lado de la API porque así la versión web avisa igual, en vez de fallar
//! pregunta a pregunta, y porque el diagnóstico depende del `config.yaml` que
//! el motor ya tiene cargado.

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
