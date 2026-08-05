"""Modelos de datos que comparten todas las etapas del pipeline.

Estos tipos son el vocabulario común del motor: el loader produce Documents,
el chunker los convierte en Chunks, el retriever devuelve RetrievedChunks y el
generator produce un Answer. Nada acá depende de Qdrant, Ollama ni del dominio
de los documentos.
"""

from pydantic import BaseModel, Field


class Document(BaseModel):
    """Un archivo de texto leído del disco, todavía sin partir."""

    # Ruta relativa a la carpeta de documentos. Es lo que se le muestra al
    # usuario como fuente, así que no queremos rutas absolutas de la máquina.
    source_file: str
    content: str


class Chunk(BaseModel):
    """Un fragmento de documento, la unidad que se embebe y se busca."""

    text: str
    source_file: str
    # Ruta de headers markdown hasta este fragmento, de más general a más
    # específico. Ej: ["MOVE — PRD", "3. Core Features", "3.4 Correlation"].
    # Vacía si el documento no tiene headers (ej. un .txt plano).
    header_path: list[str] = Field(default_factory=list)
    # Posición del chunk dentro de su documento de origen, empezando en 0.
    chunk_index: int
    # Offset en caracteres dentro del documento original. Permite ubicar el
    # fragmento en el archivo real si alguien quiere verificar la cita.
    char_start: int

    @property
    def citation(self) -> str:
        """Cita legible: 'archivo.md § Sección > Subsección'."""
        if not self.header_path:
            return self.source_file
        return f"{self.source_file} § {' > '.join(self.header_path)}"


class RetrievedChunk(BaseModel):
    """Un chunk recuperado de la búsqueda, con su score de similitud."""

    chunk: Chunk
    # Similitud coseno con la pregunta, en [0, 1]. Más alto = más relevante.
    score: float


class Source(BaseModel):
    """Una fuente citada, tal como se le devuelve al usuario.

    Se construye desde la metadata del chunk recuperado, nunca desde el texto
    que generó el modelo: así una fuente no puede ser inventada.
    """

    source_file: str
    section: str
    score: float
    # Fragmento del texto que respaldó la respuesta, para poder verificarla.
    excerpt: str


class Answer(BaseModel):
    """Respuesta final del motor."""

    question: str
    answer: str
    sources: list[Source] = Field(default_factory=list)
    # True cuando el motor determinó que no tenía contexto suficiente. Permite
    # al cliente (API, widget) distinguir un "no sé" honesto de una respuesta.
    insufficient_context: bool = False
