"""Tests del retriever.

El foco está en las dos decisiones que toma: si hay contexto suficiente para
responder, y si ese contexto es lo bastante bueno como para no advertir.
"""

from core.config import RetrievalConfig
from core.retriever import Retriever
from tests.conftest import FakeEmbeddingClient, FakeStore, make_retrieved

CONFIG = RetrievalConfig(top_k=5, min_score_threshold=0.5, low_confidence_threshold=0.65)


def build_retriever(results) -> Retriever:
    return Retriever(FakeEmbeddingClient(), FakeStore(results), CONFIG)


def test_retrieve_returns_store_results():
    expected = [make_retrieved(0.9), make_retrieved(0.8)]
    retriever = build_retriever(expected)

    assert retriever.retrieve("una pregunta") == expected


def test_retrieve_embeds_the_question():
    """La pregunta se vectoriza con el mismo cliente que indexó los documentos."""
    client = FakeEmbeddingClient()
    retriever = Retriever(client, FakeStore([]), CONFIG)

    retriever.retrieve("¿cómo funciona?")

    assert client.embedded == ["¿cómo funciona?"]


def test_retrieve_honors_explicit_top_k():
    results = [make_retrieved(0.9 - i / 100) for i in range(10)]
    retriever = build_retriever(results)

    assert len(retriever.retrieve("pregunta", top_k=3)) == 3


def test_no_results_means_no_context():
    retriever = build_retriever([])

    assert retriever.has_sufficient_context([]) is False


def test_score_above_threshold_is_sufficient():
    retriever = build_retriever([])

    assert retriever.has_sufficient_context([make_retrieved(0.7)]) is True


def test_score_below_threshold_is_insufficient():
    """La barrera determinista: por debajo del mínimo no se consulta al modelo."""
    retriever = build_retriever([])

    assert retriever.has_sufficient_context([make_retrieved(0.3)]) is False


def test_score_exactly_at_threshold_is_sufficient():
    """El umbral es inclusivo: justo en el límite se responde."""
    retriever = build_retriever([])

    assert retriever.has_sufficient_context([make_retrieved(0.5)]) is True


def test_only_best_score_decides():
    """Los resultados vienen ordenados, así que solo importa el primero."""
    retriever = build_retriever([])
    results = [make_retrieved(0.9), make_retrieved(0.1), make_retrieved(0.05)]

    assert retriever.has_sufficient_context(results) is True


def test_low_confidence_between_thresholds():
    """Entre el mínimo y el de confianza se responde, pero advirtiendo."""
    retriever = build_retriever([])
    results = [make_retrieved(0.55)]

    assert retriever.has_sufficient_context(results) is True
    assert retriever.is_low_confidence(results) is True


def test_high_score_is_not_low_confidence():
    retriever = build_retriever([])

    assert retriever.is_low_confidence([make_retrieved(0.8)]) is False


def test_empty_results_are_low_confidence():
    retriever = build_retriever([])

    assert retriever.is_low_confidence([]) is True
