# Imagen de la API de Mnemosyne.
# Build en dos etapas para que la imagen final no cargue con las herramientas
# de compilación.

FROM python:3.12-slim AS builder

WORKDIR /build

# Solo se copian los archivos de dependencias primero: mientras no cambien,
# Docker reutiliza la capa de instalación aunque cambie el código. Los
# __init__ van porque setuptools verifica que los paquetes declarados existan.
COPY pyproject.toml ./
COPY core/__init__.py core/
COPY api/__init__.py api/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install .


FROM python:3.12-slim

# Usuario sin privilegios: el proceso no necesita root.
RUN useradd --create-home --shell /bin/bash mnemosyne

WORKDIR /app

COPY --from=builder /install /usr/local

COPY core/ core/
COPY api/ api/
COPY config.yaml ./

USER mnemosyne

EXPOSE 8000

# Los servicios externos se resuelven por nombre dentro de la red de compose;
# se pueden sobreescribir con variables de entorno.
ENV MNEMOSYNE_QDRANT__URL=http://qdrant:6333 \
    MNEMOSYNE_OLLAMA__URL=http://host.docker.internal:11434 \
    PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
