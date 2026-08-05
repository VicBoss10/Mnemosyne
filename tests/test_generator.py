"""Tests del contexto que se arma para el modelo.

No se prueba qué responde el modelo —eso no es determinista— sino qué se le
entrega, que es lo que el motor sí controla.
"""

import httpx
from conftest import make_retrieved

from core.config import OllamaConfig
from core.generator import Generator, build_context, strip_context_labels


def test_context_labels_each_fragment_with_its_source():
    """La cita del modelo sale de esta etiqueta: sin ella no puede nombrar la
    fuente y la respuesta deja de ser verificable."""
    chunks = [
        make_retrieved(0.9, "Contenido A", source_file="manual.pdf", header_path=["Página 12"]),
        make_retrieved(0.8, "Contenido B", source_file="guia.md", header_path=["Intro"]),
    ]

    context = build_context(chunks)

    assert "manual.pdf § Página 12" in context
    assert "guia.md § Intro" in context
    assert "Contenido A" in context
    assert "Contenido B" in context


def test_context_does_not_number_fragments():
    """Un modelo pequeño copia la etiqueta que ve. Un '[6]' en la respuesta no
    le dice nada a quien la lee: la lista de fuentes se renderiza aparte."""
    chunks = [make_retrieved(0.9, f"Contenido {i}") for i in range(3)]

    context = build_context(chunks)

    for i in range(1, 4):
        assert f"[{i}]" not in context


def test_context_preserves_retrieval_order():
    """El orden es el del ranking: el fragmento más relevante va primero."""
    chunks = [
        make_retrieved(0.9, "Primero", source_file="a.md"),
        make_retrieved(0.5, "Segundo", source_file="b.md"),
    ]

    context = build_context(chunks)

    assert context.index("Primero") < context.index("Segundo")


def test_empty_context_is_empty_string():
    assert build_context([]) == ""


def test_long_descriptive_filename_is_trimmed_in_the_label():
    """Regresión: el nombre del archivo se repite una vez por fragmento, y un
    modelo pequeño acabó respondiendo con el nombre del ARCHIVO cuando se le
    pidió el nombre del autor."""
    chunks = [
        make_retrieved(
            0.5,
            "NARVÁEZ BURBANO VÍCTOR MANUEL",
            source_file="Informe Final - Proyecto - Narváez Víctor.pdf",
            header_path=["Página 1"],
        )
    ]

    context = build_context(chunks)

    assert "Narváez Víctor.pdf" not in context
    assert "Informe Final - Proyecto.pdf" in context
    # El contenido del fragmento nunca se toca.
    assert "NARVÁEZ BURBANO VÍCTOR MANUEL" in context


def test_hyphenated_names_are_not_trimmed():
    """'README-move.md' es un nombre entero: acortarlo a 'README.md' señalaría
    un archivo distinto del que en realidad se citó."""
    chunks = [make_retrieved(0.5, "Contenido", source_file="README-move.md")]

    assert "README-move.md" in build_context(chunks)


def test_short_filenames_are_left_alone():
    for name in ["PRD.md", "manual.pdf", "service-inventory.md"]:
        chunks = [make_retrieved(0.5, "Contenido", source_file=name)]
        assert name in build_context(chunks)


# --- limpieza de etiquetas filtradas -----------------------------------------


def test_leaked_context_label_is_stripped():
    """En respuestas largas el modelo copia la línea "Fuente: ..." del contexto
    como si fuera texto. No es una cita: muestra el nombre recortado, que no
    corresponde a ningún archivo real."""
    answer = strip_context_labels(
        "La bibliografía está en las páginas 110 y 111.\n\n"
        "Fuente: Informe Final - Proyecto.pdf § Página 111"
    )

    assert "Fuente:" not in answer
    assert answer == "La bibliografía está en las páginas 110 y 111."


def test_leaked_label_with_dashes_is_stripped():
    """El separador de fragmentos se filtra con los guiones incluidos."""
    answer = strip_context_labels("Respuesta.\n--- Fuente: manual.pdf § Página 3")

    assert "Fuente:" not in answer
    assert answer == "Respuesta."


def test_prose_citations_are_preserved():
    """Una cita escrita en prosa es exactamente lo que el prompt pide: solo se
    elimina la línea de etiqueta reproducida literalmente."""
    text = "El asesor fue Fulano, según manual.pdf, página 12."

    assert strip_context_labels(text) == text


def test_stripping_leaves_no_blank_line_holes():
    answer = strip_context_labels("Primera parte.\n\nFuente: a.pdf § Página 1\n\nSegunda parte.")

    assert "\n\n\n" not in answer
    assert "Primera parte." in answer
    assert "Segunda parte." in answer


def make_streaming_generator(tokens: list[str]) -> Generator:
    """Generator cuyo Ollama emite los tokens dados, sin red."""
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        lines = [json.dumps({"message": {"content": t}}) for t in tokens]
        lines.append(json.dumps({"message": {"content": ""}, "done": True}))
        return httpx.Response(200, text="\n".join(lines))

    config = OllamaConfig()
    generator = Generator(config)
    generator._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=config.url
    )
    return generator


def test_streamed_label_split_across_tokens_is_stripped():
    """En streaming la etiqueta llega repartida entre tokens: un filtro por
    token no la vería, así que la salida se libera por líneas completas."""
    generator = make_streaming_generator(
        ["La bibliografía", " está en la página 111.\n", "Fuente:", " informe", ".pdf\n"]
    )

    answer = "".join(generator.generate_stream("¿la bibliografía?", [make_retrieved(0.9)]))

    assert "Fuente:" not in answer
    assert "La bibliografía está en la página 111." in answer


def test_streaming_emits_the_whole_answer_when_nothing_leaks():
    """La retención por líneas no puede perder texto, incluida la última línea
    si no termina en salto."""
    generator = make_streaming_generator(["Primera línea.\n", "Segunda", " sin salto final"])

    answer = "".join(generator.generate_stream("pregunta", [make_retrieved(0.9)]))

    assert "Primera línea." in answer
    assert "Segunda sin salto final" in answer
