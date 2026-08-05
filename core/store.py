"""Vector store: chunk persistence and search in Qdrant.

Qdrant holds each chunk's vector alongside its metadata (the payload). A search
compares the question's vector against the stored ones and returns the nearest
by cosine similarity.

Search is hybrid: each chunk carries both a dense vector (meaning) and a sparse
BM25 one (literal terms), and Qdrant fuses the two rankings. Dense search alone
misses rare literal tokens in short questions — see core/lexical.py — and the
fusion recovers them without the fragile threshold tuning that trying to fix it
on the dense side alone requires.

The entire Qdrant dependency is confined to this module; the rest of the engine
only knows Chunk and RetrievedChunk.
"""

import logging
import math
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    Filter,
    Fusion,
    FusionQuery,
    IsEmptyCondition,
    PayloadField,
    PointStruct,
    Prefetch,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from core.config import QdrantConfig
from core.models import Chunk, RetrievedChunk

logger = logging.getLogger(__name__)

DISTANCE_MAP = {
    "cosine": Distance.COSINE,
    "euclid": Distance.EUCLID,
    "dot": Distance.DOT,
}

#: Named vectors. Qdrant requires names once a point carries more than one.
DENSE_VECTOR = "dense"
SPARSE_VECTOR = "lexical"

#: How many candidates each arm contributes before fusion. Wider than the final
#: top_k on purpose: a chunk that ranks poorly on one arm and well on the other
#: is exactly what hybrid search exists to recover, and it has to survive the
#: prefetch to be fused at all.
PREFETCH_MULTIPLIER = 5
MIN_PREFETCH = 40

#: Fixed id of the point holding the BM25 statistics. Fixed so re-ingesting
#: overwrites it instead of accumulating copies.
LEXICAL_MODEL_POINT_ID = "00000000-0000-0000-0000-000000000001"

#: Payload key marking that point. Its presence is what distinguishes metadata
#: storage from an actual chunk, so searches can filter it out.
LEXICAL_MODEL_KEY = "_lexical_model"

#: Matches only real chunks: the metadata point is the one where the marker key
#: is set, so requiring it to be empty excludes it from searches and counts.
CHUNK_ONLY = Filter(must=[IsEmptyCondition(is_empty=PayloadField(key=LEXICAL_MODEL_KEY))])


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity, so a fused result reports a score on the same scale as
    a purely dense one. Done here rather than with numpy: it runs over top_k
    vectors, not the corpus, and the engine has no numpy dependency."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


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
            vectors_config={
                DENSE_VECTOR: VectorParams(size=self.vector_size, distance=self.distance)
            },
            sparse_vectors_config={
                # on_disk=False: the sparse index is small and stays in memory,
                # where the lexical arm has to be as fast as the dense one.
                SPARSE_VECTOR: SparseVectorParams(index=SparseIndexParams(on_disk=False))
            },
        )
        logger.info(
            "Collection '%s' created (dim=%d, distance=%s)",
            self.collection_name,
            self.vector_size,
            self.distance,
        )

    def upsert_chunks(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        sparse_vectors: list[tuple[list[int], list[float]]] | None = None,
    ) -> None:
        """Store chunks together with their dense and sparse vectors.

        sparse_vectors is optional so a caller that only has dense embeddings
        still works; those chunks are then reachable by meaning alone.

        Raises:
            ValueError: if the number of chunks and embeddings differ.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Chunk count ({len(chunks)}) and embedding count ({len(embeddings)}) differ"
            )
        if sparse_vectors is not None and len(sparse_vectors) != len(chunks):
            raise ValueError(
                f"Chunk count ({len(chunks)}) and sparse vector count "
                f"({len(sparse_vectors)}) differ"
            )
        if not chunks:
            return

        points = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            vector: dict = {DENSE_VECTOR: embedding}
            if sparse_vectors is not None:
                indices, values = sparse_vectors[i]
                vector[SPARSE_VECTOR] = SparseVector(indices=indices, values=values)

            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    # The payload carries the whole chunk, so a search can rebuild
                    # the citation without going back to disk.
                    payload=chunk.model_dump(),
                )
            )

        self.client.upsert(collection_name=self.collection_name, points=points)
        logger.info("Indexed %d chunks into '%s'", len(points), self.collection_name)

    def search(
        self,
        query_vector: list[float],
        top_k: int,
        sparse_query: tuple[list[int], list[float]] | None = None,
    ) -> list[RetrievedChunk]:
        """Return the top_k best chunks for the question.

        With a sparse query, both arms run and Qdrant fuses them with Reciprocal
        Rank Fusion. RRF combines by *rank*, not score, which is what makes the
        fusion possible at all: cosine similarity and BM25 live on different,
        unnormalizable scales.
        """
        if sparse_query is not None and sparse_query[0]:
            return self._hybrid_search(query_vector, top_k, sparse_query)

        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            using=DENSE_VECTOR,
            limit=top_k,
            query_filter=CHUNK_ONLY,
            with_payload=True,
        ).points

        return self._to_chunks(results)

    def _hybrid_search(
        self,
        query_vector: list[float],
        top_k: int,
        sparse_query: tuple[list[int], list[float]],
    ) -> list[RetrievedChunk]:
        """Dense and lexical arms fused by RRF, server-side.

        RRF decides the *order*, but its score is a rank artifact (~0.016) on a
        scale unrelated to cosine similarity. The thresholds and the interface
        both read scores as cosine, so each fused result is re-scored with its
        real dense similarity. Order stays RRF's; the number stays meaningful.
        """
        indices, values = sparse_query
        prefetch_limit = max(MIN_PREFETCH, top_k * PREFETCH_MULTIPLIER)

        results = self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[
                Prefetch(
                    query=query_vector,
                    using=DENSE_VECTOR,
                    limit=prefetch_limit,
                    filter=CHUNK_ONLY,
                ),
                Prefetch(
                    query=SparseVector(indices=indices, values=values),
                    using=SPARSE_VECTOR,
                    limit=prefetch_limit,
                    filter=CHUNK_ONLY,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            query_filter=CHUNK_ONLY,
            with_payload=True,
            with_vectors=[DENSE_VECTOR],
        ).points

        return [
            RetrievedChunk(
                chunk=Chunk(**point.payload),
                score=_cosine(query_vector, point.vector[DENSE_VECTOR]),
            )
            for point in results
            if point.payload and point.vector
        ]

    @staticmethod
    def _to_chunks(points) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(chunk=Chunk(**point.payload), score=point.score)
            for point in points
            if point.payload
        ]

    def save_lexical_model(self, model_data: dict) -> None:
        """Persist the fitted BM25 statistics alongside the index.

        They live in Qdrant rather than in a file so index and statistics cannot
        drift apart: dropping the collection drops them too, and a query never
        scores against term frequencies from a corpus that no longer exists.
        """
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=LEXICAL_MODEL_POINT_ID,
                    # A zero vector: this point is storage, never a search result.
                    # It is excluded from queries by the marker in its payload.
                    vector={DENSE_VECTOR: [0.0] * self.vector_size},
                    payload={LEXICAL_MODEL_KEY: model_data},
                )
            ],
        )

    def load_lexical_model(self) -> dict | None:
        """Read back the persisted BM25 statistics, or None if absent.

        Absent means an index built before hybrid search existed; the caller
        falls back to dense-only rather than failing.
        """
        if not self.client.collection_exists(self.collection_name):
            return None

        points = self.client.retrieve(
            collection_name=self.collection_name,
            ids=[LEXICAL_MODEL_POINT_ID],
            with_payload=True,
        )
        if not points or not points[0].payload:
            return None
        return points[0].payload.get(LEXICAL_MODEL_KEY)

    def count(self) -> int:
        """Number of indexed chunks, or 0 when the collection does not exist."""
        if not self.client.collection_exists(self.collection_name):
            return 0
        return self.client.count(
            collection_name=self.collection_name, count_filter=CHUNK_ONLY
        ).count

    def health_check(self) -> bool:
        """True if Qdrant responds."""
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False
