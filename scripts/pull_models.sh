#!/usr/bin/env bash
# Descarga los modelos que necesita Mnemosyne.
# Es el único momento en que el sistema usa internet: una vez bajados, todo
# corre offline.
#
# Usa el Ollama nativo del host por defecto. Si Ollama corre en contenedor
# (perfil "ollama" del compose), invocar con:
#   OLLAMA_CONTAINER=mnemosyne-ollama ./scripts/pull_models.sh

set -euo pipefail

CONFIG="$(dirname "$0")/../config.yaml"

# Los modelos se leen del config.yaml para no duplicar la configuración.
EMBEDDING_MODEL="${MNEMOSYNE_EMBEDDING_MODEL:-$(grep -E '^\s+embedding_model:' "$CONFIG" | awk '{print $2}')}"
GENERATION_MODEL="${MNEMOSYNE_GENERATION_MODEL:-$(grep -E '^\s+generation_model:' "$CONFIG" | awk '{print $2}')}"

# Con OLLAMA_CONTAINER definido se ejecuta dentro del contenedor; si no,
# contra el binario nativo.
if [[ -n "${OLLAMA_CONTAINER:-}" ]]; then
    ollama_cmd() { docker exec "$OLLAMA_CONTAINER" ollama "$@"; }
    echo "Usando Ollama en el contenedor: $OLLAMA_CONTAINER"
else
    if ! command -v ollama >/dev/null 2>&1; then
        echo "ERROR: no se encontró el comando 'ollama'." >&2
        echo "Instalalo con:  curl -fsSL https://ollama.com/install.sh | sh" >&2
        exit 1
    fi
    ollama_cmd() { ollama "$@"; }
    echo "Usando Ollama nativo del host"
fi

echo "Modelo de embeddings: $EMBEDDING_MODEL"
echo "Modelo de generación: $GENERATION_MODEL"
echo

for model in "$EMBEDDING_MODEL" "$GENERATION_MODEL"; do
    echo ">>> Descargando $model ..."
    ollama_cmd pull "$model"
done

echo
echo "Modelos disponibles:"
ollama_cmd list
