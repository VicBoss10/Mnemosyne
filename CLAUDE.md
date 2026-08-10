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
(ej. un proyecto sobre normativa, otro sobre la documentación de un producto),
y en la fase 2 cada proyecto se puede exponer como un widget de chat embebible
en cualquier sitio web externo.

## Por qué existe (contexto para quien lea el repo)

Corre local para no depender de costos por consulta ni de que los datos
salgan de la máquina del usuario — un argumento real para cualquier empresa
que quiera IA sin exponer información sensible a terceros.

El repositorio no incluye ningún corpus: los documentos son datos del usuario
y viven fuera de git (ver `.gitignore`). `config.yaml` viene con valores
genéricos y se apunta a la carpeta propia con `project.docs_path`.

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
- **El motor es agnóstico de dominio.** Nada del código central —ni de la
  configuración por defecto, ni de los tests— puede asumir de qué tratan los
  documentos. El repo no lleva corpus ni nombres de ningún proyecto concreto.

## Arquitectura

Fase 1 (MVP, prioridad total) y Fase 2 (stretch, solo si el tiempo alcanza)
están separadas intencionalmente — ver "Fases de desarrollo" abajo.

```
core/           → motor RAG en Python: ingesta, chunking, embeddings,
                   vector store, retrieval, generación
                   (independiente de cómo se exponga al exterior)
api/            → FastAPI que expone el motor core como servicio HTTP/WS
                   y sirve la interfaz web
desktop/        → app nativa (Tauri): lanza Qdrant y la API como procesos
                   hijos y carga su interfaz en una ventana. No contiene
                   lógica del motor
gateway/        → [Fase 2] servicio en Go — auth por proyecto, rate
                   limiting, multi-tenant, streaming hacia el widget
widget/         → [Fase 2] widget embebible en TypeScript + Web Components
                   (Shadow DOM) para insertar en sitios externos
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
| Embeddings | modelo servido por Ollama (`bge-m3`) | Un solo runtime (Ollama) para embeddings + generación, menos piezas móviles. Multilingüe: obligatorio con corpus en español — ver decisiones abajo |
| Generación | Ollama (ej. `llama3.1:8b` o `qwen2.5:7b`, cuantizado GGUF) | Elegir modelo según hardware disponible |
| Vector store | Qdrant, lanzado por la app como proceso hijo | No usar el modo embebido del cliente — correr como servicio real, aunque sea local. El binario viaja en el instalador |
| Empaquetado | Tauri (Rust + webview del sistema) | Un instalador por plataforma: `.AppImage`/`.deb` y `.msi`/NSIS. Lleva el motor y el vector store adentro |
| [Fase 2] Gateway | Go | Multi-tenant, auth por API key, rate limiting, streaming |
| [Fase 2] Widget | TypeScript + Web Components (Shadow DOM) | Debe funcionar embebido sin chocar con el CSS del sitio anfitrión |
| CI | GitHub Actions | Lint + tests en cada push |

## Fases de desarrollo

**Fase 1 — MVP (prioridad total, esto es lo que se termina primero):**
1. Estructura del proyecto + `core` con vectores/chunks/ingesta funcionando
   por script o CLI simple
2. Integración con Qdrant como servicio local
3. Integración con Ollama para generación, con prompt que restringe al
   contexto y exige citar fuente
4. Endpoint FastAPI simple (`POST /query`) que expone todo esto
5. App de escritorio que levanta Qdrant + la API con un doble clic
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

**Fase 1 completa.** En curso: multi-proyecto — un mismo motor sirviendo varios
corpus aislados, con un dashboard para administrarlos.

- [x] Estructura de carpetas inicial
- [x] `core`: ingesta y chunking (md, txt, PDF y DOCX)
- [x] `core`: embeddings + Qdrant
- [x] `core`: retrieval + generación con Ollama
- [x] API FastAPI (incluye streaming SSE e interfaz web de chat)
- [x] App de escritorio (Tauri): instaladores con el motor y Qdrant adentro
- [x] Tests del núcleo (187 tests, sin dependencias externas)
- [x] README con quickstart
- [x] Instaladores de Windows, compilados en CI y probados en Windows real
- [x] Multi-proyecto: registro, un pipeline por corpus, endpoints HTTP y
      dashboard con crear / indexar / entrar / borrar
- [ ] Multi-proyecto: re-indexado incremental (hoy se reconstruye entero)
- [ ] (Fase 2) Gateway en Go
- [ ] (Fase 2) Widget en TypeScript

### Decisiones tomadas durante el desarrollo

Cosas que no son obvias leyendo el código y conviene no re-litigar:

- **Ollama es la única dependencia que no se empaqueta.** Pesa ~1,4 GB en Linux
  y Windows por las librerías de CUDA, y los modelos suman varios gigabytes más:
  el instalador pasaría de 5 GB para traer algo que en la mayoría de las
  máquinas ya está, duplicando pesos que Ollama ya gestiona. La app comprueba si
  responde y guía la instalación cuando falta. Qdrant sí viaja adentro: es un
  binario suelto de ~30 MB sin dependencias.
- **No hay Docker en ningún lado, y es deliberado.** El compose se eliminó al
  pasar a la app nativa: dos formas de levantar lo mismo significan dos formas
  de que se rompa. Si la Fase 2 necesita un despliegue servidor, se recupera del
  historial de git.
- **Modelo de 3B por restricción de VRAM.** La GPU de desarrollo es una GTX 1650
  con 4 GB; `llama3.1:8b` no entra junto al modelo de embeddings.
- **El modelo de embeddings es multilingüe (`bge-m3`), y eso no es negociable
  con un corpus en español.** Con `nomic-embed-text` —entrenado sobre todo en
  inglés— dos textos españoles *sin ninguna relación* ya puntuaban ~0.50, así que
  el rango útil era una franja de ~0.15 y el ruido ganaba: en una prueba real un
  pasaje de otro tema puntuó 0.706 contra 0.663 del pasaje que tenía la
  respuesta. `bge-m3` baja ese piso a ~0.37. Costo: 1024 dimensiones en vez de
  768, y es más pesado.
- **Los umbrales se calibraron midiendo, y las poblaciones SÍ se solapan.**
  Cambiar de modelo de embeddings no elimina el solapamiento: lo que separa los
  scores es la longitud de la pregunta, no su validez: una pregunta corta lleva
  poco contenido semántico y puntúa bajo por serlo. Medido con `bge-m3` e
  híbrido: legítimas 0.326-0.672, ajenas 0.312-0.364. Por eso
  `min_score_threshold` es 0.25 — un piso contra ruido, no un juicio de
  relevancia — y `low_confidence_threshold` 0.37, apenas encima de la banda
  ajena. **Cuidado con calibrar sobre preguntas largas y descriptivas**: dan una
  brecha falsa que desaparece con el fraseo corto que la gente escribe de verdad.
  Con híbrido hay una razón extra para no subirlos: un fragmento puede llegar el
  primero por coincidencia léxica exacta con un coseno bajo, así que un umbral
  alto marcaría como dudosas respuestas correctas — y un aviso que salta siempre
  es un aviso que nadie lee.
- **Rechazar es trabajo del prompt, no del umbral.** Verificado saltándose el
  umbral por completo: "receta del arroz con leche", "quién es Napoleón" y
  "mundial de 1986" siguen respondiendo "No tengo esa información". Subir el
  umbral no aporta seguridad; solo bloquea preguntas válidas.
- **La búsqueda es híbrida: densa + BM25 léxico, fusionadas con RRF en Qdrant.**
  El lado denso difumina los términos literales raros, y una pregunta corta le da
  poco con qué trabajar: en un caso medido, una pregunta corta traía el fragmento
  correcto en el **puesto 36** —fuera de todo `top_k` razonable— aunque el término
  preguntado estaba literalmente en el texto. Con el brazo léxico sube al
  **puesto 1**. BM25 está
  implementado en `core/lexical.py` (sin dependencias nuevas) y sus estadísticas
  se guardan como un punto dentro de la propia colección, para que índice y
  estadísticas no se puedan desincronizar. Cambiar el chunking o los documentos
  obliga a re-ingerir.
- **RRF ordena, pero el score que se reporta es el coseno denso.** El score de
  RRF (~0.016) es un artefacto del rango y no vive en la escala que los umbrales
  y la interfaz interpretan. Por eso `_hybrid_search` re-puntúa cada resultado
  con su similitud densa real: el orden lo decide la fusión, el número sigue
  significando algo.
- **El nombre del archivo se recorta en la etiqueta del contexto.** Se repite una
  vez por fragmento, y con `top_k: 10` un nombre descriptivo largo aparece diez
  veces: el modelo de 3B empezó a leerlo como dato y respondía el nombre del
  *archivo* cuando se le pedía el nombre del autor. Se recorta solo el sufijo
  descriptivo (` - Algo`), nunca un nombre con guiones sin espacios como
  `README-api.md`, que señalaría un archivo distinto. La fuente completa sí
  llega al usuario: se renderiza desde la metadata, no desde esta etiqueta.
- **Hay contenido que ninguna búsqueda alcanza, y conviene saberlo.** La portada
  de un PDF nombra al autor pero no contiene la palabra "autor" —es un título, un
  nombre y una universidad—, así que no comparte ningún término con la pregunta y
  ni el brazo denso ni el léxico la enganchan. Un humano lo infiere de la
  maquetación; la extracción a texto plano destruye esa señal. `top_k: 10` la
  rescata para las preguntas más directas, pero es un límite real del enfoque, no
  un bug pendiente.
- **La confianza se decide con dos señales, no solo con el coseno.** Con híbrido
  el coseno dejó de bastar: una pregunta corta válida puntuó **0.307** —por debajo
  de una ajena a 0.364— pero el brazo léxico pone el fragmento correcto en el
  puesto 1. La segunda señal es el **solape léxico**: qué fracción de los
  términos de la pregunta aparece literal en un mismo fragmento recuperado. Medido
  aquí separa perfecto: legítimas ≥0.5, ajenas exactamente 0.0. Basta que una de
  las dos señales pase para considerar la respuesta confiable. Antes el aviso
  salía en 2 de 8 respuestas correctas; ahora en 0 de 8, y sigue saliendo en 4 de
  4 ajenas.
- **Ningún documento puede ocupar más del 60% del contexto recuperado.** Los
  corpus reales no están balanceados: aquí un PDF de 115 páginas es el **81.5%**
  del índice y un `.md` corto el **1.4%**, así que el grande copaba el ranking y
  el pequeño con la respuesta directa no aparecía nunca — "¿qué herramienta se usó
  concretas devolvían 10 de 10 fragmentos del PDF. El límite reserva
  sitio sin reordenar por relevancia, y lo que excede no se descarta: rellena la
  cola, para que una pregunta que de verdad responde un solo documento conserve
  todo su contexto.
- **El prompt distingue "no hay nada" de "hay algo parcial".** La regla era
  binaria y el modelo hacía las dos cosas a la vez: daba el dato y a continuación
  afirmaba "no tengo esa información". Ahora hay una regla explícita para el caso
  parcial —dar lo que haya y decir qué falta— y la prohibición de contradecirse.
- **El modelo copia la etiqueta `Fuente:` del contexto en respuestas largas.** No
  es una cita: muestra el nombre *recortado*, que no corresponde a ningún archivo
  real. Se limpia en post-proceso (`strip_context_labels`). En streaming la
  etiqueta llega partida entre tokens, así que la salida se retiene y se libera
  por líneas completas — un filtro por token no la vería.
- **El contexto que ve el modelo no lleva fragmentos numerados.** Un modelo
  pequeño copia la etiqueta que ve y respondía "según el fragmento [6]", que no
  significa nada para quien lee. Los bloques se etiquetan solo con su fuente; la
  lista numerada se renderiza aparte, en la capa de presentación.
- **Pregunta y documento se embeben con roles distintos** (`embed_query` /
  `embed_documents`). Varios modelos se entrenan de forma asimétrica: una
  pregunta y el pasaje que la responde no se parecen como texto, y el modelo solo
  los acerca si cada uno lleva su marcador. `bge-m3` no los necesita (prefijos
  vacíos); `nomic-embed-text` sí (`search_query: ` / `search_document: `). Cambiar
  los prefijos invalida el índice — hay que re-ingerir.
- **Los párrafos sobredimensionados se cortan por frase, no por carácter.** Una
  página de PDF llega como un solo bloque de 2000-3800 caracteres; el corte ciego
  por tamaño partía palabras a la mitad y ese token truncado no aporta significado
  y diluye el vector. Ver `_split_by_sentence` en `core/chunker.py`.
- **El chunker descarta secciones con cuerpo casi vacío.** No es solo higiene:
  esos fragmentos repiten el nombre del proyecto en el título, puntúan alto en
  cualquier búsqueda que lo mencione y desplazan al contenido con la respuesta.
- **Los extractores devuelven Markdown, no texto plano.** PDF y DOCX no se
  aplanan: las páginas del PDF y los estilos de encabezado de Word se traducen a
  headers `#`. Así el chunker y el sistema de citas funcionan sin cambios, y una
  cita de PDF apunta a una página verificable. Agregar un formato nuevo = una
  función en `core/extractors.py` + una entrada en `EXTRACTORS`; el loader no se
  toca.
- **El texto de PDF se re-une por líneas.** El extractor emite un salto de línea
  donde el layout tenía uno, a mitad de frase incluida. Sin re-unir, un párrafo
  llega como una docena de líneas sueltas y el chunker lee límites de párrafo que
  no existen.
- **Precedencia de configuración: entorno > YAML.** Requiere
  `settings_customise_sources` en `core/config.py`; sin eso pydantic prioriza
  los valores del constructor y las variables de entorno no surten efecto. De
  eso depende la app de escritorio por completo: levanta Qdrant en un puerto que
  elige el sistema al arrancar, así que la dirección no se conoce a tiempo de
  escribirla en ningún archivo y solo puede llegar por entorno.
- **Cada proyecto es una colección de Qdrant (`mnemosyne_{slug}`), y ese es todo
  el aislamiento.** No hay filtro por metadata que se pueda olvidar en una
  consulta: si la colección es la equivocada, no hay respuesta que devolver.
  `Workspace` mantiene un pipeline por proyecto y los reutiliza —abren conexiones
  a Ollama y a Qdrant, construirlos por consulta sería caro— y copia los settings
  en vez de mutarlos, porque la base la comparten todos.
- **Borrar un proyecto borra su índice, nunca sus documentos.** Son del usuario y
  viven en su carpeta. El registro (`data_dir/projects.json`) se escribe de forma
  atómica con `.tmp` + `replace()`.
- **La tarjeta muestra los contadores del registro, no de Qdrant.** Listar ocurre
  cada vez que se abre el dashboard, y una consulta al vector store por proyecto
  haría que el coste creciera con el número de proyectos.
- **Una carpeta que desaparece no rompe nada: solo impide re-indexar.** El índice
  vive en Qdrant, así que el chat sigue respondiendo; la tarjeta lo avisa con
  `folder_exists` antes de que la acción falle.
- **`/dependencies` no puede depender de que exista un proyecto.** Es lo que
  llama el asistente de primer arranque, y ahí no hay ninguno — justo cuando más
  importa saber si falta Ollama. Los modelos son configuración, no corpus:
  `Pipeline.check_dependencies(settings)` responde sin abrir nada.
- **Las fuentes se empaquetan, como Qdrant.** La interfaz usa dos familias
  (Noto Serif para marca, títulos y la prosa del chat; Noto Sans para los
  controles), servidas desde `api/static/fonts/` como subconjuntos latinos en
  WOFF2 — ~140 KB las cuatro variantes, contra ~2,3 MB de los `.ttf`. No es
  estética por encima del principio de cero internet: un `@import` a Google
  Fonts lo rompería, y depender de las fuentes del sistema no es opción cuando
  la app se distribuye en instaladores para Linux y Windows. Son SIL OFL y el
  texto de la licencia viaja con ellas (`fonts/OFL.txt`); se regeneran con
  fonttools desde las Noto del sistema. `--add-data` de PyInstaller copia
  `api/static` entero, así que la carpeta viaja al instalador sin tocar el build.
- **La serif no es decorativa: marca qué es prosa y qué es interfaz.** Pregunta
  y respuesta van en serif porque se leen de corrido; botones, etiquetas,
  fuentes citadas y estado se quedan en sans porque se escanean. Por eso la
  jerarquía del chat se ve incluso en blanco y negro. El cuerpo de la respuesta
  además se topa a `70ch`: la columna son 980px y sin ese tope la línea llegaba
  a ~100 caracteres, donde el ojo pierde el renglón al volver a la izquierda.
- **Los tamaños salen de una escala de siete pasos (`--t-xs`…`--t-2xl`).** Antes
  convivían trece tamaños distintos entre 11 y 22px, elegidos uno a uno según
  hiciera falta; cada uno era razonable por separado y el conjunto se leía como
  improvisado, porque no había ritmo reconocible. Añadir un tamaño nuevo fuera
  de la escala revive ese problema.
- **En la tarjeta solo puede haber un `margin-top: auto`.** Estaba a la vez en
  `.stats` y en `.actions`, y en una columna flex únicamente el primer margen
  automático se lleva el sobrante: el de `.actions` no hacía nada y, en cuanto
  una tarjeta de la fila llevaba nota de aviso y otra no, la línea de cifras se
  descolgaba entre tarjetas vecinas.
- **La capa nativa se quedó con lo que solo ella puede hacer.** `pick_docs_folder`
  abre el diálogo del sistema y nada más; el registro de proyectos y la
  indexación son de la API. Tener las dos mitades recordando cuál es "la carpeta"
  era garantía de que se desincronizaran.
