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
2. Si los fragmentos no contienen la información necesaria, responde \
exactamente: "{INSUFFICIENT_CONTEXT_MESSAGE}"
3. Cita los fragmentos que usaste con su número entre corchetes: [1], [2].
4. No inventes datos, cifras, nombres ni rutas de archivos que no estén en los \
fragmentos.
5. Responde en español neutro, de forma clara y concisa.\
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
    """Assemble the numbered context block handed to the model.

    Each fragment is labelled with its number and origin. The origin helps the
    model write more precise answers ("according to the manual...").
    """
    blocks = []
    for i, retrieved in enumerate(chunks, start=1):
        blocks.append(f"[{i}] Fuente: {retrieved.chunk.citation}\n{retrieved.chunk.text}")
    return "\n\n".join(blocks)


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

        content = response.json().get("message", {}).get("content", "").strip()
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
                        yield piece
                    if data.get("done"):
                        break
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
