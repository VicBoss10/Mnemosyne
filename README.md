# Mnemosyne

A retrieval-augmented question answering engine that runs **entirely on your own
machine**. Point it at a folder of documents, ask questions in natural language,
and get answers grounded in that content — each one citing the document and
section it came from. When the documents don't contain the answer, Mnemosyne
says so instead of inventing one.

No cloud APIs, no per-query costs, no data leaving the host.

```
$ ./mnemosyne ask "What services does the docker-compose bring up?"

The services are: backend, move_db, keycloak_db, keycloak, frontend and pgadmin.

Fuentes:
  [1] service-inventory.md § Inventario de servicios  (similitud: 0.826)
  [2] README-move.md § MOVE — ... > Cómo Empezar      (similitud: 0.786)
```

The engine is domain-agnostic: a "project" is just a folder of documents plus a
configuration block. Nothing in `core/` knows what the documents are about.

## Stack

| Component | Technology |
|---|---|
| Engine | Python 3.11+, FastAPI |
| Embeddings | `bge-m3` via Ollama (1024 dimensions, multilingual) |
| Generation | `qwen2.5:3b-instruct-q4_K_M` via Ollama |
| Vector store | Qdrant (Docker) |
| Interface | Dependency-free HTML/CSS/JS, served by the API |
| Tests | pytest (123 tests, no external dependencies) |

## Requirements

- Docker and Docker Compose
- Python 3.11+
- [Ollama](https://ollama.com) running on the host
- ~3 GB of free disk for the models
- An NVIDIA GPU is optional; without one Ollama falls back to CPU

## Deployment

The system has three pieces, deployed differently:

| Piece | How it runs | Starts on boot |
|---|---|---|
| Ollama | systemd service on the host | yes |
| Qdrant | Docker container | yes (`restart: unless-stopped`) |
| API + engine | Python venv, or a container | venv: no · container: yes |

### First-time setup

```bash
# 1. Ollama and the models (once; the only step that uses the network)
curl -fsSL https://ollama.com/install.sh | sh
./scripts/pull_models.sh

# 2. Vector store
docker compose up -d qdrant

# 3. Python environment
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 4. Index the documents
./mnemosyne ingest

# 5. Ask, or start the web interface
./mnemosyne ask "What is MOVE?"
./mnemosyne serve      # http://localhost:8100
```

The installer registers Ollama as a systemd service, so it comes back on every
boot and needs no further attention. If it ever stops responding:

```bash
systemctl status ollama
sudo systemctl restart ollama
```

Qdrant likewise restarts with Docker. Only the API is started by hand in this
mode — which is the point, since it is the piece under active development.

### Running the API in a container

For a deployment where nothing depends on the venv:

```bash
docker compose up -d                                    # Qdrant + API
docker compose exec api python -m core.cli ingest       # first-time indexing
```

The interface is then at `http://localhost:8100`. Documents and `config.yaml`
are bind-mounted, not baked into the image, so changing them needs no rebuild —
but re-indexing is still required after changing documents.

Override the host port with `MNEMOSYNE_PORT` if 8100 is taken:

```bash
MNEMOSYNE_PORT=9000 docker compose up -d
```

### Commands

| Command | Purpose |
|---|---|
| `./mnemosyne ingest` | Index the configured document set |
| `./mnemosyne ask "..."` | Ask a question |
| `./mnemosyne serve` | Start the web interface and API |
| `./mnemosyne status` | Show configuration and system state |

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Chat interface |
| `GET` | `/project` | Active project's title and sample questions |
| `POST` | `/query` | Question and complete answer |
| `GET` | `/query/stream` | Answer streamed as Server-Sent Events |
| `POST` | `/ingest` | Re-index the document set |
| `GET` | `/health` | System state |
| `GET` | `/docs` | Interactive OpenAPI documentation |

### Using your own documents

Put your documents in a folder and point [`config.yaml`](config.yaml) at it:

| Format | Extensions | How structure becomes citations |
|---|---|---|
| Markdown / text | `.md`, `.markdown`, `.txt` | Markdown headers → `file.md § Section > Subsection` |
| Word | `.docx` | Heading styles → the same header hierarchy |
| PDF | `.pdf` | One section per page → `manual.pdf § Página 3` |

Word tables are indexed as Markdown tables. Scanned PDFs with no text layer are
skipped with a warning — extracting them would need OCR, which is out of scope.
Anything else in the folder (images, `.doc`, spreadsheets) is ignored.


```yaml
project:
  name: my-project           # names the Qdrant collection
  docs_path: path/to/docs
```

Then re-run `./mnemosyne ingest`. Ingestion rebuilds the collection from
scratch, so running it twice is harmless. Each project gets its own collection,
so several can share one Qdrant instance.

Any setting can be overridden by an environment variable using the `MNEMOSYNE_`
prefix and a double underscore for nesting — precedence is **environment >
`.env` > `config.yaml` > defaults**. See [`.env.example`](.env.example).

### Why Ollama is not containerized

`docker-compose.yml` brings up Qdrant and the API, but **not** Ollama, which is
expected to run natively on the host.

When Docker is installed as a snap, confinement prevents the daemon from reading
the NVIDIA driver binaries under `/usr/bin`, and the Ollama container fails to
start with GPU access — reporting a missing file that is plainly there. Running
natively sidesteps that and avoids duplicating ~2 GB of model weights inside a
Docker volume.

If your Docker can reach the GPU, an optional profile is available. Point the
API at `http://ollama:11434` when using it:

```bash
docker compose --profile ollama up -d
OLLAMA_CONTAINER=mnemosyne-ollama ./scripts/pull_models.sh
```

## How it works

**Ingestion** — documents are split along Markdown headers (which double as
citation metadata), each fragment is embedded, and both vector and metadata are
stored in Qdrant.

**Query** — the question is embedded with the same model, the nearest fragments
are retrieved, and a prompt containing only those fragments is sent to the
generator.

Retrieval is **hybrid**: a dense vector for meaning and a sparse BM25 one for
literal terms, fused by Qdrant with Reciprocal Rank Fusion. Dense search alone
blurs rare literal tokens, and short questions give it little to work with — the
question "¿quién es el asesor?" retrieved the passage containing the word
"Asesor" at **rank 36**, far outside any sensible `top_k`. With the lexical arm
it ranks **first**. BM25 is implemented in [`core/lexical.py`](core/lexical.py)
with no added dependency, and its corpus statistics are stored inside the
collection itself so index and statistics cannot drift apart.

RRF decides the ordering, but its score is a rank artifact on a scale unrelated
to cosine similarity, so each fused result is re-scored with its real dense
similarity — the ordering stays RRF's, the number stays comparable.

A final pass caps how much of the context one document may occupy
(`max_document_share`). Corpora are rarely balanced — in the demo set a 115-page
PDF is 81% of the index and a short `.md` 1.4% — so without the cap the large
document crowds out the small one that holds the direct answer. Fragments beyond
the cap refill the tail rather than being dropped, so a question genuinely
answered by a single document still gets its full context.

Three independent mechanisms keep answers grounded:

1. The prompt restricts the model to the supplied context.
2. A similarity threshold answers "no information" **without invoking the
   model** when retrieval comes up short — deterministic, not dependent on the
   model complying.
3. Sources are built from retrieved-chunk metadata, never parsed out of model
   output, so a citation cannot be fabricated.

`config.yaml` exposes two thresholds: `min_score_threshold` (below it, don't
answer) and `low_confidence_threshold` (below it, answer but flag it as worth
verifying). Two are needed because the score ranges of legitimate and unrelated
questions **overlap**, and no cutoff separates them. What drives the score is
question length, not validity: a short question carries little semantic content
and scores low for that reason alone. Measured on the demo corpus, legitimate
questions span 0.326-0.672 and unrelated ones 0.312-0.364.

So `min_score_threshold` is a floor against noise (0.25), not a relevance
judgement. Refusing is the prompt's job, and bypassing the threshold entirely
confirms it: unrelated questions are still answered "no information". The
overlap is handled by the second threshold, which surfaces a dubious answer with
a warning and its sources rather than dropping it.

Hybrid search adds a reason to keep both low: a fragment can now rank first on an
exact lexical match while its dense score stays low, so a high threshold would
flag correct answers as dubious — and a warning that fires on everything is one
nobody reads.

That is also why the low-confidence flag uses a **second signal**: how much of
the question appears verbatim in a single retrieved fragment
(`min_lexical_overlap`). If the word "asesor" is literally in the text, relevance
is not in doubt whatever the cosine says. Measured here the separation is clean —
legitimate questions reach ≥0.5, unrelated ones exactly 0.0 — so either signal
clearing its threshold marks the answer as confident.

**Changing the embedding model requires recalibrating both**, by measuring real
scores for questions that should and should not be answered. Calibrate with the
short phrasings users actually type — long descriptive questions score high and
suggest a gap that isn't there.

### On the choice of embedding model

The engine answers over Spanish documents, and the embedding model has to be
multilingual for that to work. With the English-centric `nomic-embed-text`,
two *entirely unrelated* Spanish texts already scored ~0.50 similarity, leaving
a usable band of roughly 0.15 in which noise won: for the question "who was the
project advisor?", an unrelated passage scored 0.706 against 0.663 for the
passage actually naming the advisor. `bge-m3` lowers that noise floor to ~0.37
and ranks the two correctly.

Models trained asymmetrically also need role markers — the question and the
passage answering it do not resemble each other as text. `query_prefix` and
`document_prefix` supply them (`bge-m3` needs none; `nomic-embed-text` expects
`search_query: ` and `search_document: `). Changing them invalidates the index,
so re-run ingestion.

## Development

```bash
pytest          # 123 tests, ~1.6s
ruff check .
```

Tests replace Qdrant and Ollama with fakes (see [`tests/conftest.py`](tests/conftest.py)),
so the suite is deterministic and needs no running infrastructure.

## Status

**Phase 1 (MVP) is complete**: ingestion, chunking, embeddings, vector store,
retrieval, generation, CLI, HTTP API with SSE streaming, web interface, tests
and Docker packaging.

Known limitations: scanned PDFs need OCR and are skipped, the legacy `.doc`
format is not supported, documents are loaded by copying them into the
configured folder, and re-indexing is manual.

**Phase 2**, not started: a Go gateway for multi-tenant projects and API keys,
and an embeddable TypeScript widget using Shadow DOM.

## License

Not yet specified.
