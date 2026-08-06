#!/usr/bin/env bash
# Descarga los modelos que necesita Mnemosyne.
# Es el único momento en que el sistema usa internet: una vez bajados, todo
# corre offline.
#
# La app de escritorio hace lo mismo desde su interfaz, con barra de progreso.
# Este script es para usar el motor sin ella.

set -euo pipefail

CONFIG="$(dirname "$0")/../config.yaml"

# Los modelos se leen del config.yaml para no duplicar la configuración.
EMBEDDING_MODEL="${MNEMOSYNE_EMBEDDING_MODEL:-$(grep -E '^\s+embedding_model:' "$CONFIG" | awk '{print $2}')}"
GENERATION_MODEL="${MNEMOSYNE_GENERATION_MODEL:-$(grep -E '^\s+generation_model:' "$CONFIG" | awk '{print $2}')}"

if ! command -v ollama >/dev/null 2>&1; then
    echo "ERROR: no se encontró el comando 'ollama'." >&2
    echo "Instalalo con:  curl -fsSL https://ollama.com/install.sh | sh" >&2
    exit 1
fi
ollama_cmd() { ollama "$@"; }

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
