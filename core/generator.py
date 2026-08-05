"""Answer generation from the retrieved context.

This module holds the system's central guarantee: the model may only answer with
what is in the context. Two independent mechanisms enforce it:

  1. The prompt explicitly restricts the model to the supplied context.
  2. The pipeline stops before reaching this module when retrieval fails to
     clear the similarity threshold (see pipeline.py). That second barrier is
     deterministic — it does not depend on the model complying.

Sources returned to the user never come from the generated text but from the
retrieved chunks' metadata, so a citation cannot be fabricated.

The prompts below are written in Spanish because that is the language the engine
answers in; they are user-facing content, not documentation.
"""

import json
import logging
import re
from collections.abc import Iterator

import httpx

from core.config import OllamaConfig
from core.models import RetrievedChunk

logger = logging.getLogger(__name__)

#: Verbatim answer when there is not enough context. Kept as a constant so the
#: message stays consistent and the tests can assert on it.
INSUFFICIENT_CONTEXT_MESSAGE = "No tengo esa información en los documentos disponibles."

SYSTEM_PROMPT = f"""\
Eres un asistente que responde preguntas basándose ÚNICAMENTE en los fragmentos \
de documentación que se te entregan.

Reglas estrictas:
1. Responde usando exclusivamente la información de los fragmentos. No uses \
conocimiento previo tuyo, aunque estés seguro de la respuesta.
2. Si los fragmentos no contienen NADA sobre lo que se pregunta, responde \
exactamente: "{INSUFFICIENT_CONTEXT_MESSAGE}" y nada más.
3. Si los fragmentos responden solo en parte, da lo que sí encuentres y di qué \
falta. Nunca ambas cosas a la vez: no respondas con un dato y a continuación \
afirmes que no tienes la información.
4. Cuando cites, nombra la fuente como aparece en "Fuente:" del fragmento \
(por ejemplo, "según manual.pdf, página 12"). Nunca te refieras a un fragmento \
por su número: quien lee la respuesta no ve esa numeración.
5. El nombre de un archivo no es un dato del contenido. Si la pregunta pide un \
nombre, una fecha o una cifra, tómalos del texto de los fragmentos, nunca del \
nombre del documento.
6. No inventes datos, cifras, nombres ni rutas de archivos que no estén en los \
fragmentos.
7. Responde en español neutro, de forma clara y concisa.\
"""

USER_PROMPT_TEMPLATE = """\
Fragmentos de documentación:

{context}

---

Pregunta: {question}

Responde basándote solo en los fragmentos de arriba."""


class GenerationError(RuntimeError):
    """Raised when Ollama fails to generate an answer."""


def build_context(chunks: list[RetrievedChunk]) -> str:
    """Assemble the context block handed to the model.

    Each fragment is labelled with its origin, which is what the model should
    name when citing — see _short_name for why that label is trimmed. Fragments
    are deliberately *not* numbered: a small model copies whatever label it sees,
    and "[6]" means nothing to the person reading the answer. The caller renders
    the real source list separately.
    """
    blocks = []
    for retrieved in chunks:
        chunk = retrieved.chunk
        section = " § " + " > ".join(chunk.header_path) if chunk.header_path else ""
        blocks.append(f"--- Fuente: {_short_name(chunk.source_file)}{section}\n{chunk.text}")

    return "\n\n".join(blocks)


#: Trailing " - Something" in a filename, requiring whitespace around the dash.
#: Descriptive names often end in the author's or a project's name, which is what
#: a small model latches onto when asked for a name. The whitespace requirement
#: keeps hyphenated identifiers intact: "README-move.md" is one name, not a name
#: with a suffix, and shortening it to "README.md" would misidentify the source.
FILENAME_SUFFIX = re.compile(r"\s+[-–—]\s+[^-–—]+$")


def _short_name(source_file: str) -> str:
    """Shorten a filename for the context label.

    A label is repeated once per fragment, so a long descriptive filename gets
    restated ten times and a small model starts reading it as content: asked for
    the author's full name, it answered with the name in the *filename* instead
    of the one on the cover page. Trimming the descriptive tail keeps the label
    citable without turning it into the loudest string in the prompt.

    The full name still reaches the user: sources are rendered from chunk
    metadata, not from this label.
    """
    stem, _, extension = source_file.rpartition(".")
    if not stem:
        return source_file

    shortened = FILENAME_SUFFIX.sub("", stem)
    # Never shorten into something meaningless.
    if len(shortened) < 4:
        shortened = stem

    return f"{shortened}.{extension}"


#: A context label reproduced verbatim in the answer: the "--- Fuente: ..." line
#: that separates fragments in the prompt. On long answers the model copies it as
#: if it were part of the text.
LEAKED_LABEL = re.compile(r"^\s*-*\s*Fuente:.*$", re.MULTILINE)


def strip_context_labels(text: str) -> str:
    """Remove context labels the model copied into its answer.

    The label is prompt scaffolding, not content: it shows the *shortened*
    filename, which does not name a real file, and the caller renders the true
    sources from chunk metadata anyway. A citation the model writes in prose
    ("según el informe, página 111") is left untouched — only the verbatim label
    line is dropped.
    """
    cleaned = LEAKED_LABEL.sub("", text)
    # Collapse the blank lines the removal leaves behind.
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


class Generator:
    """Client for Ollama's generation endpoint."""

    def __init__(self, config: OllamaConfig) -> None:
        self.config = config
        self._client = httpx.Client(base_url=config.url, timeout=config.timeout)

    def _build_payload(self, question: str, chunks: list[RetrievedChunk], stream: bool) -> dict:
        """Build the request body for /api/chat."""
        prompt = USER_PROMPT_TEMPLATE.format(context=build_context(chunks), question=question)
        return {
            "model": self.config.generation_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": stream,
            "options": {
                "temperature": self.config.temperature,
                "num_ctx": self.config.num_ctx,
            },
        }

    def generate(self, question: str, chunks: list[RetrievedChunk]) -> str:
        """Generate the complete answer from the question and its context.

        Raises:
            GenerationError: if Ollama is unreachable, rejects the request, or
                returns an empty answer.
        """
        if not chunks:
            return INSUFFICIENT_CONTEXT_MESSAGE

        try:
            response = self._client.post(
                "/api/chat", json=self._build_payload(question, chunks, stream=False)
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GenerationError(
                f"Ollama rejected the generation request ({exc.response.status_code}). "
                f"Is the model '{self.config.generation_model}' downloaded? "
                f"Try: ollama pull {self.config.generation_model}"
            ) from exc
        except httpx.RequestError as exc:
            raise GenerationError(
                f"Could not reach Ollama at {self.config.url}. Is it running? Try: ollama serve"
            ) from exc

        content = strip_context_labels(response.json().get("message", {}).get("content", ""))
        if not content:
            raise GenerationError("Ollama returned an empty answer")

        return content

    def generate_stream(self, question: str, chunks: list[RetrievedChunk]) -> Iterator[str]:
        """Generate the answer token by token.

        Ollama returns one JSON object per line, from which the new text
        fragment is extracted. This lets the interface render the answer as it
        is produced instead of waiting for completion.

        Raises:
            GenerationError: if Ollama is unreachable or rejects the request.
        """
        if not chunks:
            yield INSUFFICIENT_CONTEXT_MESSAGE
            return

        payload = self._build_payload(question, chunks, stream=True)
        buffer = ""

        try:
            with self._client.stream("POST", "/api/chat", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        # One corrupt line must not abort the whole answer.
                        logger.warning("Unparseable line from Ollama: %r", line[:100])
                        continue

                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        # A leaked label arrives split across tokens, so it is
                        # only recognisable once its line is complete. Text is
                        # buffered up to the last newline and released a line at
                        # a time; the tail of the current line waits.
                        buffer += piece
                        *complete, buffer = buffer.split("\n")
                        for line_text in complete:
                            if not LEAKED_LABEL.match(line_text):
                                yield line_text + "\n"
                    if data.get("done"):
                        break

            if buffer and not LEAKED_LABEL.match(buffer):
                yield buffer
        except httpx.HTTPStatusError as exc:
            raise GenerationError(
                f"Ollama rejected the generation request ({exc.response.status_code}). "
                f"Is the model '{self.config.generation_model}' downloaded? "
                f"Try: ollama pull {self.config.generation_model}"
            ) from exc
        except httpx.RequestError as exc:
            raise GenerationError(
                f"Could not reach Ollama at {self.config.url}. Is it running? Try: ollama serve"
            ) from exc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Generator":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
