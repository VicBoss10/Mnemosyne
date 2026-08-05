"""Carga de configuración: config.yaml como base, variables de entorno encima.

El orden de precedencia es: variables de entorno > config.yaml > defaults del
código. Esto permite que el mismo config.yaml sirva para desarrollo local y
dentro de Docker, cambiando solo las URLs por entorno.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Raíz del repo, para resolver rutas relativas del config.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class ProjectConfig(BaseModel):
    name: str = "default"
    docs_path: str = "documents"


class OllamaConfig(BaseModel):
    url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    generation_model: str = "qwen2.5:3b-instruct-q4_K_M"
    temperature: float = 0.1
    num_ctx: int = 8192
    timeout: int = 180


class QdrantConfig(BaseModel):
    url: str = "http://localhost:6333"
    distance: str = "cosine"


class ChunkingConfig(BaseModel):
    max_chunk_size: int = 1200
    overlap: int = 150
    min_chunk_size: int = 50


class RetrievalConfig(BaseModel):
    top_k: int = 5
    min_score_threshold: float = 0.45


class Settings(BaseSettings):
    """Configuración completa del motor."""

    model_config = SettingsConfigDict(
        env_prefix="MNEMOSYNE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    project: ProjectConfig = Field(default_factory=ProjectConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)

    @property
    def collection_name(self) -> str:
        """Nombre de la colección en Qdrant, derivado del proyecto.

        Cada proyecto vive en su propia colección, así una sola instancia de
        Qdrant puede servir a varios proyectos sin mezclarlos.
        """
        return f"mnemosyne_{self.project.name}"

    @property
    def resolved_docs_path(self) -> Path:
        """docs_path resuelto a ruta absoluta contra la raíz del repo."""
        path = Path(self.project.docs_path)
        return path if path.is_absolute() else REPO_ROOT / path


def load_settings(config_path: Path | None = None) -> Settings:
    """Construye Settings desde un YAML, dejando que el entorno lo sobreescriba."""
    path = config_path or DEFAULT_CONFIG_PATH
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings cacheados, para no releer el YAML en cada request."""
    return load_settings()
