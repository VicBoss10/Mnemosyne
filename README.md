# Mnemosyne

A retrieval-augmented question answering engine that runs **entirely on your own
machine**. Point it at a folder of documents, ask questions in natural language,
and get answers grounded in that content — each one citing the document and
section it came from. When the documents don't contain the answer, Mnemosyne
says so instead of inventing one.

No cloud APIs, no per-query costs, no data leaving the host.

```
$ ./mnemosyne ask "What services does the deployment bring up?"

The services are: the API, the database, the identity provider and the frontend.

Fuentes:
  [1] service-inventory.md § Inventario de servicios  (similitud: 0.826)
  [2] README-api.md § Arquitectura > Cómo empezar     (similitud: 0.786)
```

The engine is domain-agnostic: a "project" is just a folder of documents plus a
configuration block. Nothing in `core/` knows what the documents are about.

## Stack

| Component | Technology |
|---|---|
| Engine | Python 3.11+, FastAPI |
| Embeddings | `bge-m3` via Ollama (1024 dimensions, multilingual) |
| Generation | `qwen2.5:3b-instruct-q4_K_M` via Ollama |
| Vector store | Qdrant, launched by the app |
| Interface | Dependency-free HTML/CSS/JS, served by the API |
| Desktop shell | Tauri (Rust + the system webview) |
| Tests | pytest (139 tests, no external dependencies) |

## Installing

Mnemosyne ships as a desktop application: the installer carries the engine and
the vector store, so the only external dependency is
[Ollama](https://ollama.com), which is too large to bundle (~1.4 GB, plus
several more in models). The app detects whether it is present and offers to
download the missing models.

Grab the installer for your system — `.AppImage` or `.deb` on Linux, `.msi` on
Windows — or build it from source (see `desktop/README.md`):

```bash
sudo dpkg -i Mnemosyne_0.1.0_amd64.deb     # or run the .AppImage directly
```

Then open it like any other application. On first launch, pick the folder
holding your documents and the app indexes it.

Requirements: ~4 GB of free disk for the models, and an NVIDIA GPU if you want
speed — without one Ollama falls back to CPU and answers take longer.

### Where the data lives

Index, configuration and documents sit in the OS data directory, never next to
the executable:

| Path | Contents |
|---|---|
| `~/.local/share/com.mnemosyne.desktop/qdrant/` | the vector index |
| `~/.local/share/com.mnemosyne.desktop/config.yaml` | your configuration |

On Windows, under `%APPDATA%\com.mnemosyne.desktop\`.

## Running from source

The engine works on its own, without the desktop shell — this is the mode to
use while developing it. Qdrant has to be running for the CLI to reach it; the
simplest way is the binary the app already downloads:

```bash
# 1. Ollama and the models (once; the only step that uses the network)
curl -fsSL https://ollama.com/install.sh | sh
./scripts/pull_models.sh

# 2. Python environment
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 3. Vector store (the app leaves a binary in desktop/binaries/)
cd desktop && npm run fetch:qdrant && cd ..
~/.local/share/com.mnemosyne.desktop/bin/qdrant &

# 4. Index and ask
./mnemosyne ingest
./mnemosyne ask "What do these documents cover?"
./mnemosyne serve      # http://localhost:8100
```

The installer registers Ollama as a systemd service, so it comes back on every
boot and needs no further attention. If it ever stops responding:

```bash
systemctl status ollama
sudo systemctl restart ollama
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

The repository ships with **no corpus**: documents are your data, and both
`documents/` and `examples/` are git-ignored. Put your files in a folder and
point the engine at it:

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
  docs_path: documents       # git-ignored, or any absolute path
```

Or without editing the file at all — handy to keep your own paths out of the
repository (see [`.env.example`](.env.example)):

```bash
MNEMOSYNE_PROJECT__NAME=my-project
MNEMOSYNE_PROJECT__DOCS_PATH=/absolute/path/to/documents
```

Then re-run `./mnemosyne ingest`. Ingestion rebuilds the collection from
scratch, so running it twice is harmless. Each project gets its own collection,
so several can share one Qdrant instance.

Any setting can be overridden by an environment variable using the `MNEMOSYNE_`
prefix and a double underscore for nesting — precedence is **environment >
`.env` > `config.yaml` > defaults**. See [`.env.example`](.env.example).

### Why Ollama is not bundled

The installer carries Qdrant — a single dependency-free binary of ~30 MB — but
not Ollama, which is expected to be installed on the host.

Ollama weighs ~1.4 GB on Linux and Windows because of the CUDA libraries, and
the models add several gigabytes on top. Bundling it would mean an installer
past 5 GB, most of it redundant on any machine that already has it, and it would
duplicate model weights that Ollama already manages. The app checks for it at
startup and walks the user through installing it when missing.

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
measured case, a short question retrieved the passage containing the very term
being asked about at **rank 36**, far outside any sensible `top_k`. With the
lexical arm it ranks **first**. BM25 is implemented in [`core/lexical.py`](core/lexical.py)
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
(`min_lexical_overlap`). If a distinctive query term is literally in the text,
relevance is not in doubt whatever the cosine says. Measured here the separation is clean —
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
retrieval, generation, CLI, HTTP API with SSE streaming, web interface, tests,
and a desktop application that bundles the whole engine.

Known limitations: scanned PDFs need OCR and are skipped, the legacy `.doc`
format is not supported, re-indexing is manual, and the Windows installers are
untested — the code covers the platform but has only ever been built on Linux.

**Phase 2**, not started: a Go gateway for multi-tenant projects and API keys,
and an embeddable TypeScript widget using Shadow DOM.

## License

Not yet specified.
