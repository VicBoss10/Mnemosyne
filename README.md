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
| Embeddings | `nomic-embed-text` via Ollama (768 dimensions) |
| Generation | `qwen2.5:3b-instruct-q4_K_M` via Ollama |
| Vector store | Qdrant (Docker) |
| Interface | Dependency-free HTML/CSS/JS, served by the API |
| Tests | pytest (59 tests, no external dependencies) |

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

Three independent mechanisms keep answers grounded:

1. The prompt restricts the model to the supplied context.
2. A similarity threshold answers "no information" **without invoking the
   model** when retrieval comes up short — deterministic, not dependent on the
   model complying.
3. Sources are built from retrieved-chunk metadata, never parsed out of model
   output, so a citation cannot be fabricated.

`config.yaml` exposes two thresholds: `min_score_threshold` (below it, don't
answer) and `low_confidence_threshold` (below it, answer but flag it as worth
verifying). Two are needed because the score ranges for legitimate and unrelated
questions overlap — short colloquial questions carry little semantic content, so
no single cutoff separates them. **Changing the embedding model requires
recalibrating both**, by measuring real scores for questions that should and
should not be answered.

## Development

```bash
pytest          # 59 tests, ~1s
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
