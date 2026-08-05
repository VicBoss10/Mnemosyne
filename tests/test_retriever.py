"""Tests del retriever.

El foco está en las dos decisiones que toma: si hay contexto suficiente para
responder, y si ese contexto es lo bastante bueno como para no advertir.
"""

from core.config import RetrievalConfig
from core.lexical import BM25
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


def test_question_is_embedded_as_a_query():
    """La pregunta va marcada como consulta, no como documento: varios modelos
    de embeddings solo acercan pregunta y respuesta si cada una lleva su rol."""
    client = FakeEmbeddingClient()
    retriever = Retriever(client, FakeStore([]), CONFIG)

    retriever.retrieve("¿quién fue el asesor?")

    assert client.queries == ["¿quién fue el asesor?"]
    assert client.documents == []


def test_lexical_arm_is_engaged_when_the_index_has_one():
    """Regresión: "¿quién es el asesor?" recuperaba el fragmento correcto en el
    puesto 36 por búsqueda densa. El brazo léxico lo sube al 1, así que tiene
    que llegar realmente al store."""
    store = FakeStore([])
    store.lexical_model = BM25.fit(["el asesor del proyecto fue Fulano"]).to_dict()
    retriever = Retriever(FakeEmbeddingClient(), store, CONFIG)

    retriever.retrieve("¿quién es el asesor?")

    assert store.last_sparse_query is not None
    indices, values = store.last_sparse_query
    assert indices and len(indices) == len(values)


def test_retrieval_falls_back_to_dense_without_a_lexical_model():
    """Un índice construido antes del híbrido no puede dejar de responder."""
    store = FakeStore([])
    store.lexical_model = None
    retriever = Retriever(FakeEmbeddingClient(), store, CONFIG)

    retriever.retrieve("una pregunta")

    assert store.last_sparse_query is None


def test_a_broken_lexical_model_does_not_break_answering():
    """La búsqueda léxica mejora el ranking; no es un requisito para responder."""
    store = FakeStore([])
    store.lexical_model = {"malformado": True}
    retriever = Retriever(FakeEmbeddingClient(), store, CONFIG)

    retriever.retrieve("una pregunta")

    assert store.last_sparse_query is None


# --- confianza por solape léxico ---------------------------------------------


def test_verbatim_term_match_counts_as_confident_despite_a_low_score():
    """Regresión: "quien es el asesor" puntúa 0.307 —por debajo de una pregunta
    ajena a 0.364— pero el híbrido pone el fragmento correcto primero. Si la
    palabra está literalmente en el texto, la relevancia no está en duda."""
    results = [make_retrieved(0.30, "El Asesor del proyecto fue Fulano de Tal")]
    retriever = Retriever(FakeEmbeddingClient(), FakeStore(results), CONFIG)

    assert retriever.is_low_confidence(results, "quien es el asesor") is False


def test_no_lexical_overlap_stays_low_confidence():
    """Una pregunta ajena no comparte ningún término con lo recuperado."""
    results = [make_retrieved(0.30, "El sensor PMS7003 mide material particulado")]
    retriever = Retriever(FakeEmbeddingClient(), FakeStore(results), CONFIG)

    assert retriever.is_low_confidence(results, "cual es la receta del arroz") is True


def test_a_high_score_alone_is_still_enough():
    """La señal densa no se sustituye, se complementa."""
    results = [make_retrieved(0.90, "Texto sin ningún término de la pregunta")]
    retriever = Retriever(FakeEmbeddingClient(), FakeStore(results), CONFIG)

    assert retriever.is_low_confidence(results, "algo completamente distinto") is False


def test_overlap_must_come_from_a_single_fragment():
    """Términos repartidos entre fragmentos distintos son una coincidencia; lo
    que señala la respuesta es encontrarlos juntos en un mismo pasaje."""
    results = [
        make_retrieved(0.30, "El asesor de la facultad"),
        make_retrieved(0.29, "El presupuesto anual"),
        make_retrieved(0.28, "Los sensores del prototipo"),
    ]
    retriever = Retriever(FakeEmbeddingClient(), FakeStore(results), CONFIG)

    # Cada término aparece en un fragmento distinto: 1/4 cada uno, nunca 0.5.
    assert retriever.is_low_confidence(results, "asesor presupuesto sensores cronograma") is True


def test_omitting_the_question_keeps_the_previous_behaviour():
    """El parámetro es opcional: sin él solo se evalúa la señal densa."""
    results = [make_retrieved(0.30, "El Asesor fue Fulano")]
    retriever = Retriever(FakeEmbeddingClient(), FakeStore(results), CONFIG)

    assert retriever.is_low_confidence(results) is True


# --- diversidad de fuentes ----------------------------------------------------


def test_one_document_cannot_take_every_slot():
    """Los corpus rara vez están balanceados: aquí un PDF de 115 páginas produce
    el 81% del índice y un .md corto el 1.4%, así que el grande copaba el ranking
    y el pequeño con la respuesta directa no aparecía nunca."""
    results = [
        make_retrieved(0.9 - i * 0.01, f"Fragmento {i}", source_file="grande.pdf")
        for i in range(12)
    ]
    results.append(make_retrieved(0.5, "La respuesta directa", source_file="pequeno.md"))
    retriever = Retriever(FakeEmbeddingClient(), FakeStore(results), CONFIG)

    retrieved = retriever.retrieve("una pregunta")
    sources = {r.chunk.source_file for r in retrieved}

    assert "pequeno.md" in sources


def test_ordering_within_a_document_is_preserved():
    """El límite reserva sitio para otras fuentes; no reordena por relevancia."""
    results = [
        make_retrieved(0.9, "Primero", source_file="a.md"),
        make_retrieved(0.8, "Segundo", source_file="a.md"),
        make_retrieved(0.7, "Tercero", source_file="b.md"),
    ]
    retriever = Retriever(FakeEmbeddingClient(), FakeStore(results), CONFIG)

    texts = [r.chunk.text for r in retriever.retrieve("pregunta")]

    assert texts.index("Primero") < texts.index("Segundo")


def test_a_single_source_corpus_still_fills_the_limit():
    """Si una pregunta la responde un solo documento, el contexto no se recorta:
    los fragmentos que exceden el límite rellenan la cola."""
    results = [
        make_retrieved(0.9 - i * 0.01, f"Fragmento {i}", source_file="unico.pdf")
        for i in range(20)
    ]
    retriever = Retriever(FakeEmbeddingClient(), FakeStore(results), CONFIG)

    assert len(retriever.retrieve("pregunta")) == CONFIG.top_k


def test_diversification_is_a_no_op_below_the_limit():
    results = [make_retrieved(0.9, "Único", source_file="a.md")]
    retriever = Retriever(FakeEmbeddingClient(), FakeStore(results), CONFIG)

    assert len(retriever.retrieve("pregunta")) == 1
