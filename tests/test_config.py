"""Tests de la configuración.

La precedencia importa en producción: la imagen de Docker trae un config.yaml
con localhost, y el despliegue tiene que poder redirigir a otros servicios con
variables de entorno.
"""

from pathlib import Path

from core.config import Settings, load_settings

YAML = """
project:
  name: desde_yaml
qdrant:
  url: http://localhost:6333
ollama:
  url: http://localhost:11434
  generation_model: modelo-del-yaml
retrieval:
  top_k: 5
"""


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(YAML, encoding="utf-8")
    return path


def test_yaml_values_are_loaded(tmp_path):
    settings = load_settings(write_config(tmp_path))

    assert settings.project.name == "desde_yaml"
    assert settings.ollama.generation_model == "modelo-del-yaml"


def test_env_overrides_yaml(tmp_path, monkeypatch):
    """Sin esto, desplegar en Docker sería imposible."""
    monkeypatch.setenv("MNEMOSYNE_QDRANT__URL", "http://qdrant:6333")

    settings = load_settings(write_config(tmp_path))

    assert settings.qdrant.url == "http://qdrant:6333"
    # Las claves no sobreescritas conservan el valor del YAML.
    assert settings.ollama.generation_model == "modelo-del-yaml"


def test_env_overrides_nested_value_without_dropping_siblings(tmp_path, monkeypatch):
    """Sobreescribir una clave anidada no debe descartar las hermanas."""
    monkeypatch.setenv("MNEMOSYNE_OLLAMA__URL", "http://ollama:11434")

    settings = load_settings(write_config(tmp_path))

    assert settings.ollama.url == "http://ollama:11434"
    assert settings.ollama.generation_model == "modelo-del-yaml"


def test_presentation_defaults_are_domain_neutral():
    """Sin configurar, la interfaz no asume nada sobre el contenido."""
    settings = Settings()

    assert settings.project.title == "Mnemosyne"
    assert settings.project.sample_questions == []


def test_defaults_apply_when_yaml_is_missing(tmp_path):
    settings = load_settings(tmp_path / "no-existe.yaml")

    assert settings.project.name == "default"
    assert settings.retrieval.top_k > 0


def test_collection_name_is_derived_from_project():
    """Cada proyecto vive en su colección: varios conviven en un mismo Qdrant."""
    settings = Settings()
    settings.project.name = "drones"

    assert settings.collection_name == "mnemosyne_drones"


def test_relative_docs_path_resolves_against_repo_root(tmp_path):
    settings = load_settings(write_config(tmp_path))

    assert settings.resolved_docs_path.is_absolute()


def test_absolute_docs_path_is_respected(monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_PROJECT__DOCS_PATH", "/datos/documentos")

    settings = Settings()

    assert str(settings.resolved_docs_path) == "/datos/documentos"
