# Mnemosyne

A retrieval-augmented question answering engine that runs **entirely on your own
machine**. Point it at a folder of documents, ask questions in natural language,
and get answers grounded in that content — each one citing the document and
section it came from. When the documents don't contain the answer, Mnemosyne
says so instead of inventing one.

No cloud APIs, no per-query costs, no data leaving the host.

> **Status: work in progress.** Configuration, document loading, chunking and
> the shared data model are implemented and tested. Embeddings, the Qdrant
> vector store, retrieval, generation, the CLI and the HTTP API are not written
> yet — see [Project status](#project-status) for the exact breakdown.

---

## Table of contents

- [Why it exists](#why-it-exists)
- [Design principles](#design-principles)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Quickstart](#quickstart)
- [Usage](#usage)
- [Configuration](#configuration)
- [Development](#development)
- [Project status](#project-status)
- [Roadmap](#roadmap)
- [License](#license)

---

## Why it exists

Hosted RAG services charge per query and require shipping your documents to a
third party. For a company with internal documentation, contracts, or regulated
material, that is often a non-starter regardless of price.

Mnemosyne runs the whole pipeline — embeddings, vector search, and text
generation — on local hardware. The only moment it touches the network is the
initial model download.

The engine itself is domain-agnostic: a "project" is just a folder of documents
plus a configuration block. The bundled example indexes the documentation of an
IoT platform, but nothing in `core/` knows or cares what the documents are about.

## Design principles

These are constraints, not preferences. Contributions that break them will not
be merged.

1. **No network calls at runtime.** Inference, embeddings, and the vector store
   all run locally. No dependency may require an external API key to function.
2. **Answers never fabricate.** The prompt restricts the model to the retrieved
   context. If retrieval returns nothing above the similarity threshold, the
   system reports that it does not have the information — without invoking the
   generator at all.
3. **Every answer cites its source.** Document name plus section path. Citations
   are built from retrieved-chunk metadata, never parsed out of model output, so
   a source cannot be hallucinated.
4. **The engine is domain-agnostic.** No code in `core/` may assume a subject
   matter. Domain content lives only under `examples/`.

## Architecture

```
core/            RAG engine: loading, chunking, embeddings, vector store,
                 retrieval, generation. Independent of how it is exposed.
api/             FastAPI service wrapping the engine over HTTP.
examples/        Demo document sets and their configuration.
scripts/         Operational helpers (model download, etc).
tests/           pytest suite for the engine.
docker-compose.yml   Brings up Qdrant + Ollama + the API.
```

### Data flow

**Ingestion** — run once per document set, offline:

```
documents/  →  loader  →  chunker  →  embeddings (Ollama)  →  Qdrant
                                       + metadata: source file, header path
```

**Query** — run per question:

```
question  →  embedding  →  vector search (Qdrant, top-k)
                              │
                              ├─ no hit above threshold → "no information"
                              │
                              └─ prompt (context + question) → Ollama → answer + cited sources
```

### Chunking strategy

Documents are split along Markdown headers rather than at fixed character
intervals. Headers are the natural semantic boundary of a document, and they are
also precisely the metadata needed for a citation like
`PRD.md § 3. Core Features > 3.4 Correlation Analysis`. A blind fixed-size split
destroys both properties.

Sections larger than `max_chunk_size` are subdivided by paragraph with
configurable overlap, keeping the header path intact. Documents without headers
(a plain `.txt`) fall through to the paragraph splitter with an empty header
path. See [`core/chunker.py`](core/chunker.py).

## Requirements

- Docker and Docker Compose
- Python 3.11+ (only for running the CLI or tests outside Docker)
- ~6 GB of free disk for the models
- An NVIDIA GPU is optional. Without one, comment out the `deploy` block of the
  `ollama` service in [`docker-compose.yml`](docker-compose.yml) and Ollama
  falls back to CPU — slower, but functional.

## Quickstart

```bash
# 1. Bring up the vector store, the model runtime and the API
docker compose up -d

# 2. Download the models into the Ollama container (the one online step)
./scripts/pull_models.sh

# 3. Index the configured document set
docker compose exec api mnemosyne ingest

# 4. Ask a question
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "How is authentication handled between services?"}'
```

Steps 3 and 4 depend on components that are not implemented yet.

## Usage

### HTTP API

`POST /query`

```json
{
  "question": "How is authentication handled between services?",
  "top_k": 5
}
```

Response:

```json
{
  "question": "How is authentication handled between services?",
  "answer": "Services authenticate through Keycloak using ...",
  "sources": [
    {
      "source_file": "keycloak-realm.md",
      "section": "Realm configuration > Clients",
      "score": 0.82,
      "excerpt": "Each backend service is registered as a confidential client ..."
    }
  ],
  "insufficient_context": false
}
```

When nothing in the corpus is relevant, `insufficient_context` is `true`, the
answer states that the information is not available, and `sources` is empty.
Clients can use that flag to distinguish an honest "I don't know" from a real
answer.

### CLI

```bash
mnemosyne ingest          # index the configured document set
mnemosyne query "..."     # ask a question from the terminal
```

## Configuration

All settings live in [`config.yaml`](config.yaml). Nothing — paths, models,
thresholds — is hardcoded.

Any value can be overridden by an environment variable using the prefix
`MNEMOSYNE_` and a double underscore for nesting. Precedence is
**environment variables > `config.yaml` > code defaults**.

```bash
MNEMOSYNE_QDRANT__URL=http://qdrant:6333
MNEMOSYNE_OLLAMA__GENERATION_MODEL=llama3.1:8b
MNEMOSYNE_PROJECT__DOCS_PATH=examples/drones/docs
```

Copy [`.env.example`](.env.example) to `.env` as a starting point.

### Key settings

| Setting | Default | Purpose |
|---|---|---|
| `project.name` | `moveiot` | Names the Qdrant collection, so several projects share one instance without mixing |
| `project.docs_path` | `examples/moveiot/docs` | Folder to ingest, relative to the repo root or absolute |
| `ollama.embedding_model` | `nomic-embed-text` | Must match `embedding_dim` |
| `ollama.generation_model` | `qwen2.5:3b-instruct-q4_K_M` | Fits in 4 GB VRAM alongside the embedding model; use `llama3.1:8b` with more |
| `ollama.temperature` | `0.1` | Low on purpose — faithfulness to context over creativity |
| `chunking.max_chunk_size` | `1200` | Characters; larger sections are subdivided by paragraph |
| `chunking.overlap` | `150` | Characters carried between subdivisions so an idea isn't cut in half |
| `retrieval.top_k` | `5` | Chunks passed to the generator as context |
| `retrieval.min_score_threshold` | `0.45` | Below this, answer "no information" without calling the generator. Raise it to be stricter |

Switching to a different document set means changing `project.name` and
`project.docs_path`, then re-running ingestion. Each project gets its own
collection.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest          # tests
ruff check .    # lint
ruff format .   # format
```

Qdrant and Ollama can be run from Compose while the engine runs on the host —
the default `config.yaml` points at `localhost` for exactly that workflow:

```bash
docker compose up -d qdrant ollama
```

### Conventions

- Type hints everywhere; `ruff` for linting (line length 100), `pytest` for tests.
- Function and variable names in English. Comments and docstrings may be in
  Spanish or English, but consistent within a file.
- Commit messages in Spanish, `type: short description`
  (e.g. `feat: agregar chunking por párrafos`).
- No dependency that requires a cloud API key. If a library phones home by
  default, disable that behavior explicitly or don't use it.

## Project status

**Phase 1 — MVP**

- [x] Project layout, configuration, shared data model
- [x] `core`: document loading
- [x] `core`: header-aware chunking
- [x] Tests for chunking
- [ ] `core`: embeddings + Qdrant integration
- [ ] `core`: retrieval + generation via Ollama
- [ ] CLI (`mnemosyne ingest` / `mnemosyne query`)
- [ ] FastAPI endpoint
- [ ] End-to-end `docker compose` run
- [ ] Tests for retrieval

## Roadmap

**Phase 2** — not started, and deliberately gated behind a complete Phase 1.

- **Gateway (Go)** — multi-tenant projects, per-project API keys, rate limiting,
  and response streaming in front of the Python API.
- **Widget (TypeScript + Web Components)** — an embeddable chat widget using
  Shadow DOM, so it can be dropped into any external site without colliding with
  the host page's CSS.
- **CI** — lint and tests on every push via GitHub Actions.

## License

Not yet specified.
