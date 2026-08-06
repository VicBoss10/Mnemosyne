"""Configuration loading: config.yaml as the base, environment variables on top.

Precedence is environment > .env > config.yaml > code defaults, which lets one
config.yaml serve every way of running the engine, with only the service URLs
differing. The desktop app depends on it: it starts Qdrant on a port the OS
picks at launch, so the address is not known until then and cannot be written
into any file.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class ProjectConfig(BaseModel):
    """Identity and presentation of the active document set.

    `title` and `sample_questions` are configured rather than hardcoded in the
    web interface because the engine cannot assume what the documents are about.
    """

    name: str = "default"
    docs_path: str = "documents"
    title: str = "Mnemosyne"
    sample_questions: list[str] = Field(default_factory=list)


class OllamaConfig(BaseModel):
    url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    #: Role markers some embedding models require (see core/embeddings.py).
    #: bge-m3 needs none; nomic-embed-text needs "search_query: " and
    #: "search_document: ". Changing these invalidates the index — re-ingest.
    query_prefix: str = ""
    document_prefix: str = ""
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
    min_score_threshold: float = 0.50
    low_confidence_threshold: float = 0.65
    #: Fraction of the question's terms that must appear verbatim in one
    #: retrieved fragment for it to count as confident despite a low cosine.
    #: See Retriever.is_low_confidence.
    min_lexical_overlap: float = 0.5
    #: Largest share of the retrieved fragments any single document may take,
    #: so a big document does not crowd out smaller ones. See
    #: Retriever._diversify.
    max_document_share: float = 0.6


class Settings(BaseSettings):
    """Complete engine configuration."""

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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Establish the precedence: environment > .env > YAML (init) > defaults.

        Without this override pydantic favours the values passed to the
        constructor — that is, the YAML — leaving environment variables with no
        effect. That breaks the desktop app, whose YAML says localhost:6333 while
        the Qdrant it launches listens on a port picked at startup: only the
        environment can redirect it.
        """
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)

    @property
    def collection_name(self) -> str:
        """Qdrant collection for this project.

        Each project gets its own collection, so a single Qdrant instance can
        serve several of them without mixing their documents.
        """
        return f"mnemosyne_{self.project.name}"

    @property
    def resolved_docs_path(self) -> Path:
        """docs_path resolved to an absolute path against the repository root."""
        path = Path(self.project.docs_path)
        return path if path.is_absolute() else REPO_ROOT / path


def load_settings(config_path: Path | None = None) -> Settings:
    """Build Settings from a YAML file, letting the environment override it."""
    path = config_path or DEFAULT_CONFIG_PATH
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings, so the YAML is not re-read on every request."""
    return load_settings()
