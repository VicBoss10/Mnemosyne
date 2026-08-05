"""Tests del cliente de embeddings.

Ollama se sustituye por un transporte de httpx que captura la petición: así se
verifica exactamente qué texto se envía, sin depender de que el servicio esté
levantado ni de qué vectores devuelva.
"""

import httpx
import pytest

from core.config import OllamaConfig
from core.embeddings import EmbeddingClient, EmbeddingError


def make_client(config: OllamaConfig, dim: int = 4) -> tuple[EmbeddingClient, list[dict]]:
    """Cliente cuyo Ollama es falso; devuelve también los payloads capturados."""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        captured.append(payload)
        n = len(payload["input"])
        return httpx.Response(200, json={"embeddings": [[0.1] * dim for _ in range(n)]})

    client = EmbeddingClient(config)
    client._client = httpx.Client(transport=httpx.MockTransport(handler), base_url=config.url)
    return client, captured


def test_prefixes_are_applied_to_each_role():
    """El prefijo distingue pregunta de pasaje, que es su única razón de ser."""
    config = OllamaConfig(
        embedding_dim=4, query_prefix="search_query: ", document_prefix="search_document: "
    )
    client, captured = make_client(config)

    client.embed_query("¿quién es el asesor?")
    client.embed_documents(["El asesor fue Fulano."])

    assert captured[0]["input"] == ["search_query: ¿quién es el asesor?"]
    assert captured[1]["input"] == ["search_document: El asesor fue Fulano."]


def test_empty_prefixes_leave_the_text_untouched():
    """bge-m3 no lleva marcadores: el texto tiene que viajar tal cual."""
    client, captured = make_client(OllamaConfig(embedding_dim=4))

    client.embed_query("pregunta")
    client.embed_documents(["pasaje"])

    assert captured[0]["input"] == ["pregunta"]
    assert captured[1]["input"] == ["pasaje"]


def test_embed_documents_preserves_order():
    """El store aparea chunks con vectores por posición: el orden es un contrato."""
    client, captured = make_client(OllamaConfig(embedding_dim=4, document_prefix="doc: "))

    client.embed_documents(["uno", "dos", "tres"])

    assert captured[0]["input"] == ["doc: uno", "doc: dos", "doc: tres"]


def test_dimension_mismatch_is_reported():
    """Un modelo que no coincide con la colección daría búsquedas sin sentido:
    mejor fallar con un mensaje claro que depurar mal retrieval después."""
    client, _ = make_client(OllamaConfig(embedding_dim=768), dim=1024)

    with pytest.raises(EmbeddingError, match="1024"):
        client.embed_query("pregunta")


def test_empty_batch_makes_no_request():
    client, captured = make_client(OllamaConfig(embedding_dim=4))

    assert client.embed_documents([]) == []
    assert captured == []
