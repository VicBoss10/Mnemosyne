"""Lexical (keyword) search, as a complement to the dense vector search.

A dense embedding captures meaning but blurs rare literal tokens: a short
question carries little semantic content, so the one passage containing the term
being asked about can sit at rank 36 while generic prose fills the top. Lexical
search has the opposite bias — it finds the exact term and ignores meaning — so
the two together cover each other's failure mode.

The implementation is BM25, the standard keyword-ranking function, expressed as
sparse vectors so Qdrant performs the search and the fusion. No extra
dependency, and nothing leaves the machine.

BM25 needs corpus statistics (how rare each term is), so a fitted model is
persisted with the index; see `IDF_PAYLOAD_KEY`.
"""

import math
import re
import unicodedata
from collections import Counter

#: Word characters plus the accents Spanish needs; digits included so "RF-01"
#: and "SCD30" stay searchable as terms.
TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)

#: BM25 term-frequency saturation. The standard default: past a few occurrences,
#: repeating a term stops adding much.
K1 = 1.5

#: BM25 length normalization, standard default. Keeps a long chunk from ranking
#: highly just because it contains more words.
B = 0.75

#: Spanish and English function words. They appear in nearly every chunk, so
#: they carry no discriminating signal and only dilute the sparse vector. The
#: list is deliberately short: over-filtering removes real query content.
_STOPWORD_TEXT = """
    a al algo algunas algunos ante antes como con contra cual cuando de del desde donde dos
    el ella ellas ellos en entre era erais eran es esa esas ese eso esos esta estas este esto
    estos fue fueron ha han hasta hay la las le les lo los mas más me mi mis mucho muy no nos
    o os otra otro para pero poco por porque que quien quienes se sea sin sobre son su sus
    también tanto te tiene tienen todo todos tu tus un una uno unos y ya
    a an and are as at be by for from has have in is it its of on or that the this to was were
    what when where which who with
"""

STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def tokenize(text: str) -> list[str]:
    """Split text into comparable terms.

    Accents are stripped so "quién" matches "quien" — users rarely type them
    consistently, and a lexical search that misses on an accent is worse than
    useless for Spanish.
    """
    lowered = text.lower()
    # NFD splits a letter from its accent; the Mn category is the accent itself.
    decomposed = unicodedata.normalize("NFD", lowered)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")

    return [t for t in TOKEN_PATTERN.findall(stripped) if t not in STOPWORDS and len(t) > 1]


def term_id(term: str) -> int:
    """Map a term to the non-negative integer index Qdrant's sparse vectors use.

    Hashing avoids persisting a full vocabulary, at the cost of collisions —
    negligible at this corpus size against a 32-bit space.
    """
    return hash_term(term) % (2**31)


def hash_term(term: str) -> int:
    """Stable hash. Python's hash() is randomized per process and would produce
    a different index on every run, silently breaking a persisted collection."""
    h = 2166136261
    for byte in term.encode("utf-8"):
        h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
    return h


class BM25:
    """BM25 fitted over a corpus, producing sparse vectors for Qdrant.

    Documents and queries are encoded differently, as BM25 prescribes: the
    document side carries the saturated, length-normalized term frequency, and
    the query side carries the inverse document frequency. Their dot product is
    the BM25 score, which is what Qdrant computes.
    """

    def __init__(self, idf: dict[str, float], avg_length: float) -> None:
        self.idf = idf
        self.avg_length = avg_length or 1.0

    @classmethod
    def fit(cls, documents: list[str]) -> "BM25":
        """Compute term statistics over the corpus being indexed."""
        tokenized = [tokenize(doc) for doc in documents]
        total = len(tokenized) or 1
        avg_length = sum(len(t) for t in tokenized) / total

        document_frequency: Counter[str] = Counter()
        for tokens in tokenized:
            document_frequency.update(set(tokens))

        # Standard BM25 idf with the +1 that keeps it positive for terms present
        # in most documents.
        idf = {
            term: math.log(1 + (total - freq + 0.5) / (freq + 0.5))
            for term, freq in document_frequency.items()
        }
        return cls(idf, avg_length)

    def encode_document(self, text: str) -> tuple[list[int], list[float]]:
        """Sparse vector for an indexed passage: (indices, values)."""
        tokens = tokenize(text)
        if not tokens:
            return [], []

        length = len(tokens)
        counts = Counter(tokens)

        indices: list[int] = []
        values: list[float] = []
        for term, count in counts.items():
            numerator = count * (K1 + 1)
            denominator = count + K1 * (1 - B + B * length / self.avg_length)
            indices.append(term_id(term))
            values.append(numerator / denominator)

        return indices, values

    def encode_query(self, text: str) -> tuple[list[int], list[float]]:
        """Sparse vector for a question, weighted by term rarity.

        A term unseen during indexing gets the idf of a maximally rare one: it
        matches nothing, so its weight is harmless, and treating it as common
        would be wrong.
        """
        tokens = tokenize(text)
        if not tokens:
            return [], []

        default_idf = max(self.idf.values(), default=1.0)

        indices: list[int] = []
        values: list[float] = []
        for term in dict.fromkeys(tokens):
            indices.append(term_id(term))
            values.append(self.idf.get(term, default_idf))

        return indices, values

    def to_dict(self) -> dict:
        """Serializable form, persisted alongside the index."""
        return {"idf": self.idf, "avg_length": self.avg_length}

    @classmethod
    def from_dict(cls, data: dict) -> "BM25":
        return cls(data["idf"], data["avg_length"])
