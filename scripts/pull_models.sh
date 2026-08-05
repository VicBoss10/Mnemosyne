#!/usr/bin/env bash
# Descarga los modelos dentro del contenedor de Ollama.
# Es el único momento en que el sistema usa internet: una vez bajados, todo
# corre offline. Los modelos quedan en el volumen ollama_data.

set -euo pipefail

CONTAINER="${OLLAMA_CONTAINER:-mnemosyne-ollama}"

# Los modelos se leen del config.yaml para no duplicar la configuración.
CONFIG="$(dirname "$0")/../config.yaml"
EMBEDDING_MODEL="${MNEMOSYNE_EMBEDDING_MODEL:-$(grep -E '^\s+embedding_model:' "$CONFIG" | awk '{print $2}')}"
GENERATION_MODEL="${MNEMOSYNE_GENERATION_MODEL:-$(grep -E '^\s+generation_model:' "$CONFIG" | awk '{print $2}')}"

echo "Contenedor: $CONTAINER"
echo "Modelo de embeddings: $EMBEDDING_MODEL"
echo "Modelo de generación:  $GENERATION_MODEL"
echo

for model in "$EMBEDDING_MODEL" "$GENERATION_MODEL"; do
    echo ">>> Descargando $model ..."
    docker exec "$CONTAINER" ollama pull "$model"
done

echo
echo "Modelos disponibles:"
docker exec "$CONTAINER" ollama list
