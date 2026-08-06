//! Lectura de la configuración del motor desde la capa nativa.
//!
//! La app necesita saber qué modelos pide el proyecto para comprobar si están
//! descargados, y eso lo decide el `config.yaml` del usuario, que puede
//! cambiarlo. Se lee el mismo archivo que el motor —el que `api_entry.py` deja
//! en el directorio de datos en el primer arranque—, no una copia aparte, para
//! que las dos capas no puedan discrepar.
//!
//! Solo se leen los nombres de los modelos. El resto de la configuración es
//! asunto del motor y esta capa no la interpreta.

use std::path::Path;

use serde::Deserialize;

/// Valores por defecto del motor, para cuando el YAML no los fija.
const DEFAULT_EMBEDDING_MODEL: &str = "bge-m3";
const DEFAULT_GENERATION_MODEL: &str = "qwen2.5:3b-instruct-q4_K_M";

#[derive(Debug, Deserialize)]
struct OllamaSection {
    embedding_model: Option<String>,
    generation_model: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ConfigFile {
    ollama: Option<OllamaSection>,
}

/// Modelos que el proyecto necesita: el de embeddings y el de generación.
///
/// Se devuelven en ese orden porque es el de la descarga: sin embeddings no se
/// puede indexar, que es el primer paso útil.
pub fn required_models(data_dir: &Path) -> Vec<String> {
    let parsed = std::fs::read_to_string(data_dir.join("config.yaml"))
        .ok()
        .and_then(|raw| serde_yaml::from_str::<ConfigFile>(&raw).ok())
        .and_then(|config| config.ollama);

    let (embedding, generation) = match parsed {
        Some(section) => (section.embedding_model, section.generation_model),
        None => (None, None),
    };

    let mut models = vec![
        embedding.unwrap_or_else(|| DEFAULT_EMBEDDING_MODEL.to_string()),
        generation.unwrap_or_else(|| DEFAULT_GENERATION_MODEL.to_string()),
    ];

    // Un proyecto podría usar el mismo modelo para ambas cosas.
    models.dedup();
    models
}
