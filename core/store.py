"""Vector store: chunk persistence and search in Qdrant.

Qdrant holds each chunk's vector alongside its metadata (the payload). A search
compares the question's vector against the stored ones and returns the nearest
by cosine similarity.

The entire Qdrant dependency is confined to this module; the rest of the engine
only knows Chunk and RetrievedChunk.
"""

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams

from core.config import QdrantConfig
from core.models import Chunk, RetrievedChunk

logger = logging.getLogger(__name__)

DISTANCE_MAP = {
    "cosine": Distance.COSINE,
    "euclid": Distance.EUCLID,
    "dot": Distance.DOT,
}


class VectorStore:
    """Qdrant wrapper scoped to one project's collection."""

    def __init__(self, config: QdrantConfig, collection_name: str, vector_size: int) -> None:
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.distance = DISTANCE_MAP.get(config.distance.lower(), Distance.COSINE)
        self.client = QdrantClient(url=config.url)

    def recreate_collection(self) -> None:
        """Create the collection from scratch, dropping any previous one.

        Ingestion is idempotent by design: the whole index is rebuilt rather
        than synchronised document by document. At this corpus size that is
        simpler and avoids orphaned chunks left over from earlier versions of a
        file.
        """
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
            logger.info("Collection '%s' deleted", self.collection_name)

        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=self.vector_size, distance=self.distance),
        )
        logger.info(
            "Collection '%s' created (dim=%d, distance=%s)",
            self.collection_name,
            self.vector_size,
            self.distance,
        )

    def upsert_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Store chunks together with their vectors.

        Raises:
            ValueError: if the number of chunks and embeddings differ.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Chunk count ({len(chunks)}) and embedding count ({len(embeddings)}) differ"
            )
        if not chunks:
            return

        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                # The payload carries the whole chunk, so a search can rebuild
                # the citation without going back to disk.
                payload=chunk.model_dump(),
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ]

        self.client.upsert(collection_name=self.collection_name, points=points)
        logger.info("Indexed %d chunks into '%s'", len(points), self.collection_name)

    def search(self, query_vector: list[float], top_k: int) -> list[RetrievedChunk]:
        """Return the top_k chunks most similar to the question's vector."""
        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=top_k,
            with_payload=True,
        ).points

        return [
            RetrievedChunk(chunk=Chunk(**point.payload), score=point.score)
            for point in results
            if point.payload
        ]

    def count(self) -> int:
        """Number of indexed chunks, or 0 when the collection does not exist."""
        if not self.client.collection_exists(self.collection_name):
            return 0
        return self.client.count(collection_name=self.collection_name).count

    def health_check(self) -> bool:
        """True if Qdrant responds."""
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False
