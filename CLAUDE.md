# Mnemosyne

## Visión

Mnemosyne es un motor de preguntas y respuestas (RAG — Retrieval Augmented
Generation) que corre **100% en local**, sin depender de servicios en la nube
ni de APIs de pago (OpenAI, Anthropic, etc.). Se le entrega una carpeta de
documentos sobre un tema cualquiera, y responde preguntas en lenguaje natural
basándose únicamente en ese contenido — citando de qué documento y qué
sección salió cada respuesta. Si no tiene la información, lo dice
explícitamente en vez de inventar.

No es un chatbot fijo sobre un solo tema: es un motor reutilizable. Cada
conjunto de documentos + configuración es un "proyecto" independiente
(ej. un proyecto sobre drones, otro sobre la documentación de MoveIoT), y en
la fase 2 cada proyecto se puede exponer como un widget de chat embebible en
cualquier sitio web externo.

## Por qué existe (contexto para quien lea el repo)

Corre local para no depender de costos por consulta ni de que los datos
salgan de la máquina del usuario — un argumento real para cualquier empresa
que quiera IA sin exponer información sensible a terceros. El caso de
demostración usa contenido curado sobre drones (regulación de vuelo,
principios aerodinámicos, mantenimiento).

## Principios no negociables

- **Cero llamadas a internet en tiempo de ejecución.** Todo — inferencia,
  embeddings, vector store — corre en la máquina local. Ninguna dependencia
  del proyecto puede requerir una API key externa para funcionar.
- **Las respuestas nunca inventan.** El prompt al modelo siempre restringe
  la respuesta al contexto recuperado. Si no hay contexto suficiente, el
  sistema debe decir que no tiene la información.
- **Cada respuesta cita su fuente** (nombre de documento + fragmento/sección).
  Esto no es opcional — es lo que diferencia el sistema de "un chatbot
  cualquiera".
- **El motor es agnóstico de dominio.** Nada del código central puede asumir
  que el contenido es sobre drones. El dominio de drones vive únicamente en
  `examples/drones/` como caso de demostración.

## Arquitectura

Fase 1 (MVP, prioridad total) y Fase 2 (stretch, solo si el tiempo alcanza)
están separadas intencionalmente — ver "Fases de desarrollo" abajo.

```
core/           → motor RAG en Python: ingesta, chunking, embeddings,
                   vector store, retrieval, generación
                   (independiente de cómo se exponga al exterior)
api/            → FastAPI que expone el motor core como servicio HTTP/WS
                   (Fase 1: esta es la única puerta de entrada)
gateway/        → [Fase 2] servicio en Go — auth por proyecto, rate
                   limiting, multi-tenant, streaming hacia el widget
widget/         → [Fase 2] widget embebible en TypeScript + Web Components
                   (Shadow DOM) para insertar en sitios externos
examples/drones/→ documentos + config del caso de demo
docker-compose.yml → levanta todo (Qdrant + Ollama + api) con un comando
```

### Flujo de datos

**Ingesta (offline, una vez por carga de documentos):**
Documentos → `core` los parte en chunks → genera embeddings → los guarda en
Qdrant con metadata (archivo de origen, sección).

**Consulta (en cada pregunta del usuario):**
Pregunta → `core` la convierte en embedding → busca los chunks más similares
en Qdrant → arma un prompt con esos chunks + la pregunta → se lo pasa a
Ollama → genera la respuesta → se devuelve junto con las fuentes citadas.

## Stack técnico

| Componente | Tecnología | Notas |
|---|---|---|
| Motor RAG | Python 3.11+, FastAPI | Núcleo de la lógica, testeable de forma aislada |
| Embeddings | modelo servido por Ollama (ej. `nomic-embed-text`) | Un solo runtime (Ollama) para embeddings + generación, menos piezas móviles |
| Generación | Ollama (ej. `llama3.1:8b` o `qwen2.5:7b`, cuantizado GGUF) | Elegir modelo según hardware disponible |
| Vector store | Qdrant (contenedor Docker) | No usar modo embebido en memoria — correr como servicio real, aunque sea local |
| Empaquetado | Docker + docker-compose | Un solo `docker-compose up` debe levantar todo |
| [Fase 2] Gateway | Go | Multi-tenant, auth por API key, rate limiting, streaming |
| [Fase 2] Widget | TypeScript + Web Components (Shadow DOM) | Debe funcionar embebido sin chocar con el CSS del sitio anfitrión |
| CI | GitHub Actions | Lint + tests en cada push |

## Fases de desarrollo

**Fase 1 — MVP (prioridad total, esto es lo que se termina primero):**
1. Estructura del proyecto + `core` con vectores/chunks/ingesta funcionando
   por script o CLI simple
2. Integración con Qdrant (levantado vía Docker)
3. Integración con Ollama para generación, con prompt que restringe al
   contexto y exige citar fuente
4. Endpoint FastAPI simple (`POST /query`) que expone todo esto
5. `docker-compose.yml` que levanta Qdrant + Ollama + la API con un comando
6. Tests del núcleo (chunking, retrieval) con pytest
7. README con quickstart, ejemplo de uso, y explicación de arquitectura

**No avanzar a Fase 2 hasta que Fase 1 esté completa, testeada, y con README
decente.** Un MVP pulido vale más que un sistema ambicioso a medias.

**Fase 2 — stretch (solo si hay tiempo después de Fase 1):**
1. Gateway en Go: sistema de "proyectos" (cada uno con su config y API key),
   proxy hacia la API de Python, streaming de respuesta
2. Widget en TypeScript embebible, con demo real insertado en un sitio
3. CI con GitHub Actions

## Convenciones de código

- Python: type hints en todo, `ruff` para lint, `pytest` para tests. Nombres
  de funciones y variables en inglés (estándar de la industria); comentarios
  y docstrings pueden ir en español o inglés, pero consistentes dentro de
  cada archivo.
- Go (fase 2): seguir `gofmt` y las convenciones estándar del lenguaje
  (nombres de paquetes cortos, manejo explícito de errores).
- Commits: mensajes descriptivos en español, formato
  `tipo: descripción breve` (ej. `feat: agregar chunking por párrafos`).
- No hardcodear rutas, modelos, ni parámetros — todo configurable vía
  `config.yaml` o variables de entorno.
- No agregar ninguna dependencia que requiera una API key de un servicio en
  la nube. Si una librería intenta llamar a internet por defecto,
  deshabilitar esa función explícitamente o no usarla.

## Estado actual del proyecto

(Esta sección se actualiza a medida que avanza el desarrollo — mantenerla
al día es responsabilidad de cada sesión de trabajo.)

**Fase 1 completa.**

- [x] Estructura de carpetas inicial
- [x] `core`: ingesta y chunking
- [x] `core`: embeddings + Qdrant
- [x] `core`: retrieval + generación con Ollama
- [x] API FastAPI (incluye streaming SSE e interfaz web de chat)
- [x] docker-compose funcional de punta a punta
- [x] Tests del núcleo (60 tests, sin dependencias externas)
- [x] README con quickstart
- [ ] (Fase 2) Gateway en Go
- [ ] (Fase 2) Widget en TypeScript

### Decisiones tomadas durante el desarrollo

Cosas que no son obvias leyendo el código y conviene no re-litigar:

- **Ollama corre nativo en el host, no en Docker.** Con Docker instalado como
  snap, el confinamiento impide leer los binarios del driver NVIDIA en
  `/usr/bin` y el contenedor falla al arrancar con GPU. Hay un perfil opcional
  `ollama` en el compose para entornos donde sí funciona.
- **Modelo de 3B por restricción de VRAM.** La GPU de desarrollo es una GTX 1650
  con 4 GB; `llama3.1:8b` no entra junto al modelo de embeddings.
- **Los umbrales se calibraron midiendo, no a ojo.** El dato clave: los scores
  de preguntas legítimas y ajenas se superponen, así que ningún umbral las
  separa. Por eso hay dos (`min_score_threshold` y `low_confidence_threshold`) y
  la discriminación fina la hace el prompt. Recalibrar si se cambia el modelo de
  embeddings.
- **El chunker descarta secciones con cuerpo casi vacío.** No es solo higiene:
  esos fragmentos repiten el nombre del proyecto en el título, puntúan alto en
  cualquier búsqueda que lo mencione y desplazan al contenido con la respuesta.
- **Precedencia de configuración: entorno > YAML.** Requiere
  `settings_customise_sources` en `core/config.py`; sin eso pydantic prioriza
  los valores del constructor y las variables de entorno no sirven en Docker.
