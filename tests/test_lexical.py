"""Tests de la búsqueda léxica (BM25).

Cubre la mitad del retrieval híbrido que no depende de un modelo de embeddings:
el ranking por término literal, que es lo que rescata las preguntas cortas.
"""

from core.lexical import BM25, term_id, tokenize


def score(model: BM25, query: str, document: str) -> float:
    """Producto punto de los vectores dispersos, que es el score BM25."""
    q_indices, q_values = model.encode_query(query)
    d_indices, d_values = model.encode_document(document)
    document_weights = dict(zip(d_indices, d_values, strict=True))
    return sum(w * document_weights.get(i, 0.0) for i, w in zip(q_indices, q_values, strict=True))


# --- tokenización -------------------------------------------------------------


def test_accents_are_ignored():
    """Nadie escribe los acentos de forma consistente; fallar por eso haría la
    búsqueda léxica inútil en español."""
    assert tokenize("quién") == tokenize("quien")
    assert tokenize("Martínez") == tokenize("martinez")


def test_stopwords_are_dropped():
    """Aparecen en casi todos los fragmentos: no discriminan nada."""
    assert tokenize("el asesor de la universidad") == ["asesor", "universidad"]


def test_alphanumeric_identifiers_survive():
    """'SCD30' o 'RF-01' son justamente los términos raros que el lado denso
    difumina y el léxico tiene que encontrar."""
    tokens = tokenize("El sensor SCD30 cumple el RF-01")

    assert "scd30" in tokens
    assert "rf" in tokens and "01" in tokens


def test_term_id_is_stable_across_processes():
    """Un hash aleatorio por proceso rompería silenciosamente un índice ya
    escrito: los ids guardados dejarían de coincidir con los de la consulta."""
    assert term_id("asesor") == term_id("asesor")
    assert term_id("asesor") != term_id("autor")
    assert 0 <= term_id("asesor") < 2**31


# --- ranking ------------------------------------------------------------------


def test_document_with_the_query_term_ranks_above_the_rest():
    corpus = [
        "El asesor fue Alberto Ramírez Soto",
        "El sensor PMS7003 mide material particulado",
        "Docker compose levanta los servicios del proyecto",
    ]
    model = BM25.fit(corpus)

    scores = [score(model, "quien es el asesor?", d) for d in corpus]

    assert scores[0] > 0
    assert scores[1] == 0
    assert scores[2] == 0


def test_rare_terms_weigh_more_than_common_ones():
    """Es la propiedad que hace útil a BM25: un término presente en todos los
    documentos no distingue entre ellos."""
    corpus = [f"proyecto documento numero {i}" for i in range(10)]
    corpus.append("proyecto asesor")
    model = BM25.fit(corpus)

    assert model.idf["asesor"] > model.idf["proyecto"]


def test_unseen_query_term_does_not_break_encoding():
    """Una palabra que no está en el corpus no coincide con nada; tratarla como
    frecuente sería lo incorrecto."""
    model = BM25.fit(["contenido indexado"])

    indices, values = model.encode_query("palabrainexistente")

    assert len(indices) == len(values) == 1
    assert values[0] > 0


def test_empty_text_encodes_to_an_empty_vector():
    model = BM25.fit(["algo de contenido"])

    assert model.encode_document("") == ([], [])
    assert model.encode_query("   ") == ([], [])
    # Una consulta solo de stopwords tampoco aporta términos.
    assert model.encode_query("el de la") == ([], [])


def test_longer_document_is_not_favoured_by_length_alone():
    """La normalización por longitud evita que un fragmento largo gane solo por
    contener más palabras."""
    corpus = ["asesor", "asesor " + "relleno " * 100]
    model = BM25.fit(corpus)

    assert score(model, "asesor", corpus[0]) > score(model, "asesor", corpus[1])


def test_model_survives_a_round_trip():
    """Las estadísticas se persisten con el índice: deben reconstruirse iguales."""
    model = BM25.fit(["el asesor del proyecto", "los sensores del prototipo"])

    restored = BM25.from_dict(model.to_dict())

    assert restored.idf == model.idf
    assert restored.avg_length == model.avg_length
    assert restored.encode_query("asesor") == model.encode_query("asesor")
