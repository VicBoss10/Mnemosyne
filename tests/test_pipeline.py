"""Pipeline tests.

These verify the system's central promise: that it never answers without
context, and that cited sources come from what was actually retrieved.
"""

import pytest

from core.generator import INSUFFICIENT_CONTEXT_MESSAGE
from core.pipeline import Pipeline, _build_sources
from core.retriever import Retriever
from tests.conftest import (
    FakeEmbeddingClient,
    FakeGenerator,
    FakeStore,
    make_retrieved,
)


def build_pipeline(settings, results, response="Respuesta generada.") -> Pipeline:
    """Pipeline with every external dependency replaced by a double."""
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.settings = settings
    pipeline.embedding_client = FakeEmbeddingClient(dim=settings.ollama.embedding_dim)
    pipeline.store = FakeStore(results)
    pipeline.generator = FakeGenerator(response)
    pipeline.retriever = Retriever(pipeline.embedding_client, pipeline.store, settings.retrieval)
    return pipeline


# --- the anti-hallucination guarantee ----------------------------------------


def test_low_score_answers_without_calling_the_model(settings):
    """Without sufficient context the model is never consulted."""
    pipeline = build_pipeline(settings, [make_retrieved(0.2)])

    answer = pipeline.answer("¿cuál es la capital de Francia?")

    assert answer.insufficient_context is True
    assert answer.answer == INSUFFICIENT_CONTEXT_MESSAGE
    assert answer.sources == []
    assert pipeline.generator.calls == []


def test_no_results_answers_without_calling_the_model(settings):
    pipeline = build_pipeline(settings, [])

    answer = pipeline.answer("algo que no está")

    assert answer.insufficient_context is True
    assert pipeline.generator.calls == []


def test_sufficient_context_calls_the_model(settings):
    pipeline = build_pipeline(settings, [make_retrieved(0.9)])

    answer = pipeline.answer("¿qué es el sistema?")

    assert answer.insufficient_context is False
    assert answer.answer == "Respuesta generada."
    assert len(pipeline.generator.calls) == 1


def test_answer_marks_low_confidence(settings):
    """A score in the doubtful band is answered, but flagged."""
    pipeline = build_pipeline(settings, [make_retrieved(0.55)])

    answer = pipeline.answer("una pregunta ambigua")

    assert answer.insufficient_context is False
    assert answer.low_confidence is True
    assert answer.answer == "Respuesta generada."


def test_high_score_is_not_low_confidence(settings):
    pipeline = build_pipeline(settings, [make_retrieved(0.9)])

    assert pipeline.answer("pregunta clara").low_confidence is False


# --- sources -----------------------------------------------------------------


def test_sources_come_from_retrieved_chunks(settings):
    """Sources come from metadata, not from the text the model generated."""
    results = [
        make_retrieved(0.9, "Primero", source_file="a.md", header_path=["A", "Uno"]),
        make_retrieved(0.8, "Segundo", source_file="b.md", header_path=["B"]),
    ]
    # The model lies, citing a file that does not exist.
    pipeline = build_pipeline(settings, results, response="Según inventado.md, ...")

    answer = pipeline.answer("pregunta")

    assert [s.source_file for s in answer.sources] == ["a.md", "b.md"]
    assert answer.sources[0].section == "A > Uno"


def test_sources_without_headers_have_empty_section(settings):
    results = [make_retrieved(0.9, source_file="plano.txt", header_path=[])]
    pipeline = build_pipeline(settings, results)

    assert pipeline.answer("pregunta").sources[0].section == ""


def test_long_excerpt_is_truncated():
    sources = _build_sources([make_retrieved(0.9, "x" * 500)])

    assert sources[0].excerpt.endswith("...")
    assert len(sources[0].excerpt) < 500


def test_short_excerpt_is_not_truncated():
    sources = _build_sources([make_retrieved(0.9, "texto corto")])

    assert sources[0].excerpt == "texto corto"


# --- atribución: qué fragmentos sustentan de verdad la respuesta --------------


def test_only_the_fragments_behind_the_answer_are_cited():
    """La búsqueda entrega diez fragmentos y la respuesta usa uno: marcar los
    diez como citados vacía la cita de significado."""
    results = [
        make_retrieved(0.9, "El sensor SCD30 mide dióxido de carbono.", source_file="a.md"),
        make_retrieved(0.8, "La interfaz web se construyó con Angular.", source_file="b.md"),
        make_retrieved(0.7, "El despliegue usa contenedores Docker.", source_file="c.md"),
    ]

    sources = _build_sources(results, "El sensor SCD30 mide dióxido de carbono.")

    assert [s.cited for s in sources] == [True, False, False]


def test_uncited_fragments_are_still_returned():
    """No se descartan: siguen siendo el contexto que se consultó."""
    results = [
        make_retrieved(0.9, "El sensor SCD30 mide dióxido de carbono.", source_file="a.md"),
        make_retrieved(0.8, "La interfaz web se construyó con Angular.", source_file="b.md"),
    ]

    sources = _build_sources(results, "El sensor SCD30 mide dióxido de carbono.")

    assert len(sources) == 2


def test_citations_are_capped():
    """Una respuesta difusa no puede volver a marcar todo el conjunto."""
    text = "alfa beta gamma delta epsilon zeta"
    results = [make_retrieved(0.9 - i / 100, text, source_file=f"{i}.md") for i in range(8)]

    sources = _build_sources(results, text)

    assert sum(s.cited for s in sources) <= 4


def test_a_refusal_cites_nothing():
    """Si el modelo dice que no tiene la información, no hay nada que citar."""
    results = [make_retrieved(0.9, "Contenido cualquiera sobre otro asunto.")]

    sources = _build_sources(results, INSUFFICIENT_CONTEXT_MESSAGE)

    assert not any(s.cited for s in sources)


def test_an_answer_matching_nothing_falls_back_to_the_best_fragment():
    """Una respuesta parafraseada puede no compartir vocabulario; citar el mejor
    fragmento es más útil que no citar ninguno."""
    results = [
        make_retrieved(0.9, "Contenido sobre sensores.", source_file="a.md"),
        make_retrieved(0.8, "Otro contenido distinto.", source_file="b.md"),
    ]

    sources = _build_sources(results, "Sí.")

    assert [s.cited for s in sources] == [True, False]


def test_sources_are_unmarked_without_an_answer():
    """El streaming emite las fuentes antes de que el texto exista."""
    sources = _build_sources([make_retrieved(0.9)])

    assert not any(s.cited for s in sources)


# --- localizadores ------------------------------------------------------------


def test_pdf_pages_become_the_locator():
    """La página es lo que permite ir a verificar la cita en el documento."""
    results = [make_retrieved(0.9, source_file="informe.pdf", header_path=["Página 2"])]

    assert _build_sources(results)[0].locator == "página 2"


def test_documents_without_pages_locate_by_line():
    results = [make_retrieved(0.9, source_file="guia.md", header_path=["Intro"], start_line=140)]

    assert _build_sources(results)[0].locator == "línea 140"


# --- input validation --------------------------------------------------------


def test_empty_question_is_rejected(settings):
    pipeline = build_pipeline(settings, [make_retrieved(0.9)])

    with pytest.raises(ValueError):
        pipeline.answer("   ")


def test_question_is_trimmed(settings):
    pipeline = build_pipeline(settings, [make_retrieved(0.9)])

    assert pipeline.answer("  ¿qué es el sistema?  ").question == "¿qué es el sistema?"


# --- streaming ---------------------------------------------------------------


def test_stream_emits_sources_before_tokens(settings):
    """Sources are known before generation starts, so they are emitted first."""
    pipeline = build_pipeline(settings, [make_retrieved(0.9)], response="uno dos tres")

    events = list(pipeline.answer_stream("pregunta"))
    kinds = [e.event for e in events]

    assert kinds[0] == "sources"
    assert kinds[-1] == "done"
    assert "token" in kinds
    assert kinds.index("sources") < kinds.index("token")


def test_stream_without_context_skips_the_model(settings):
    pipeline = build_pipeline(settings, [make_retrieved(0.2)])

    events = list(pipeline.answer_stream("pregunta ajena"))

    assert events[0].data["insufficient_context"] is True
    assert events[0].data["sources"] == []
    tokens = [e.data["text"] for e in events if e.event == "token"]
    assert tokens == [INSUFFICIENT_CONTEXT_MESSAGE]
    assert pipeline.generator.calls == []


def test_stream_reports_low_confidence(settings):
    pipeline = build_pipeline(settings, [make_retrieved(0.55)])

    events = list(pipeline.answer_stream("pregunta"))

    assert events[0].data["low_confidence"] is True


def test_stream_tokens_reconstruct_the_answer(settings):
    pipeline = build_pipeline(settings, [make_retrieved(0.9)], response="uno dos tres")

    events = list(pipeline.answer_stream("pregunta"))
    text = " ".join(e.data["text"] for e in events if e.event == "token")

    assert text == "uno dos tres"


def test_stream_emits_citations_after_the_text(settings):
    """La atribución necesita la respuesta completa, que no existe cuando se
    emiten las fuentes: llega después, en su propio evento."""
    results = [
        make_retrieved(0.9, "El sensor SCD30 mide dióxido de carbono", source_file="a.md"),
        make_retrieved(0.8, "La interfaz usa Angular exclusivamente", source_file="b.md"),
    ]
    pipeline = build_pipeline(settings, results, response="El sensor SCD30 mide dióxido de carbono")

    events = list(pipeline.answer_stream("¿qué mide el SCD30?"))
    kinds = [e.event for e in events]
    cited = next(e for e in events if e.event == "cited")

    assert kinds.index("cited") > kinds.index("token")
    assert kinds.index("cited") < kinds.index("done")
    assert cited.data["cited"] == [0]


def test_stream_emits_error_event_on_failure(settings):
    """A model failure is reported as an event instead of breaking the stream."""
    pipeline = build_pipeline(settings, [make_retrieved(0.9)])

    def explode(question, chunks):
        raise RuntimeError("Ollama no responde")
        yield  # pragma: no cover - makes this a generator

    pipeline.generator.generate_stream = explode

    events = list(pipeline.answer_stream("pregunta"))

    assert events[-1].event == "error"
    assert "Ollama no responde" in events[-1].data["message"]


# --- ingestion ---------------------------------------------------------------


def test_ingest_rebuilds_the_collection(settings, tmp_path):
    """Ingestion rebuilds from scratch, so running it twice is idempotent."""
    (tmp_path / "doc.md").write_text(
        "# Título\n\n" + "Contenido con suficiente longitud para ser indexado. " * 5,
        encoding="utf-8",
    )
    pipeline = build_pipeline(settings, [])

    result = pipeline.ingest(tmp_path)

    assert pipeline.store.recreated is True
    assert result.documents == 1
    assert result.chunks > 0
    assert len(pipeline.store.upserted) == result.chunks


def test_ingest_without_indexable_content_fails(settings, tmp_path):
    """A folder whose documents carry no usable content must report it."""
    (tmp_path / "vacio.md").write_text("# A\n\n## B\n", encoding="utf-8")
    pipeline = build_pipeline(settings, [])

    with pytest.raises(ValueError, match="No indexable fragment"):
        pipeline.ingest(tmp_path)


def test_ingest_on_missing_folder_fails(settings, tmp_path):
    pipeline = build_pipeline(settings, [])

    with pytest.raises(FileNotFoundError):
        pipeline.ingest(tmp_path / "no-existe")


# --- calibración de los umbrales reales --------------------------------------


def test_configured_floor_admits_weakly_scoring_questions():
    """Regresión de un fallo real: "¿Quién fue el asesor?" puntuaba 0.332 y el
    umbral la rechazaba, aunque el fragmento con la respuesta venía en el top_k.

    Una pregunta corta lleva poco contenido semántico y puntúa bajo por serlo,
    no por ser inválida — de hecho puntúa menos que preguntas ajenas más largas.
    Ningún umbral separa ambas poblaciones, así que el piso solo filtra ruido y
    la discriminación queda en el prompt.
    """
    from core.config import get_settings

    floor = get_settings().retrieval.min_score_threshold

    assert floor <= 0.33, (
        f"min_score_threshold={floor} rechaza preguntas cortas legítimas; "
        "medir ambas poblaciones antes de subirlo"
    )


def test_weak_score_is_answered_but_flagged(settings):
    """El solapamiento se maneja avisando, no ocultando: la respuesta sale con
    su advertencia y sus fuentes, y quien lee decide."""
    settings.retrieval.min_score_threshold = 0.25
    settings.retrieval.low_confidence_threshold = 0.50
    pipeline = build_pipeline(settings, [make_retrieved(0.33)])

    answer = pipeline.answer("¿quién fue el asesor?")

    assert answer.insufficient_context is False
    assert answer.low_confidence is True
    assert answer.sources
    assert len(pipeline.generator.calls) == 1
