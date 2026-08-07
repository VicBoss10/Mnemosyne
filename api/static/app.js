const conversation = document.getElementById('conversation');
const form = document.getElementById('form');
const input = document.getElementById('question');
const sendButton = document.getElementById('send');

// El proyecto que este chat está consultando. Lo fija el dashboard al entrar en
// una tarjeta, y de él sale el `?project=` de cada llamada: sin eso la API
// respondería con el proyecto activo, que puede no ser el que se ve en pantalla
// si el usuario abrió otro en otra pestaña.
let currentProject = null;

/** Añade el proyecto en curso a una ruta de la API. */
function withProject(path) {
  if (!currentProject) return path;
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}project=${encodeURIComponent(currentProject)}`;
}

/** Deja el chat listo para un proyecto, vaciando la conversación anterior. */
function openChat(slug) {
  currentProject = slug;
  conversation.innerHTML = `
    <div class="empty" id="empty">
      <img class="empty-mark" src="/static/mark.png" alt="" width="64" height="61">
      <h2>Pregunta lo que quieras sobre los documentos indexados</h2>
      <p>Las respuestas salen únicamente de esos documentos, y cada una cita su fuente.</p>
      <div class="suggestions" id="suggestions"></div>
    </div>
  `;
  loadProject();
  refreshStatus();
  input.focus();
}

// El título y las preguntas de ejemplo dependen de qué documentos se hayan
// indexado, así que vienen de la configuración del proyecto: la interfaz no
// asume nada sobre el dominio del contenido.
async function loadProject() {
  try {
    const response = await fetch(withProject('/project'));
    const project = await response.json();

    if (project.title) {
      document.getElementById('project-title').textContent = project.title;
    }
    renderSuggestions(project.sample_questions || []);
  } catch {
    // Sin datos del proyecto la interfaz sigue siendo usable: solo no ofrece
    // sugerencias.
    renderSuggestions([]);
  }
}

function renderSuggestions(questions) {
  const container = document.getElementById('suggestions');
  if (!container) return;
  container.innerHTML = '';
  for (const question of questions) {
    const button = document.createElement('button');
    button.textContent = question;
    button.addEventListener('click', () => ask(question));
    container.appendChild(button);
  }
}

// --- estado del sistema -----------------------------------------------------

async function refreshStatus() {
  const el = document.getElementById('status');
  const text = document.getElementById('status-text');
  try {
    const response = await fetch(withProject('/health'));
    const data = await response.json();
    if (data.qdrant && data.indexed_chunks > 0) {
      el.className = 'ok';
      text.textContent = `${data.indexed_chunks} fragmentos indexados`;
    } else if (data.qdrant) {
      el.className = 'bad';
      text.textContent = 'sin documentos indexados';
    } else {
      el.className = 'bad';
      text.textContent = 'vector store no disponible';
    }
  } catch {
    el.className = 'bad';
    text.textContent = 'API no disponible';
  }
}

// --- utilidades -------------------------------------------------------------

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function renderSource(source, index) {
  // El localizador (página / línea) es lo que permite ir a verificar el dato en
  // el documento real, así que va junto al nombre del archivo y no escondido.
  return `
    <div class="source${source.cited ? ' cited' : ''}" data-index="${index}">
      <div class="ref">
        <span class="file">${escapeHtml(source.source_file)}</span>
        ${source.locator ? `<span class="locator">${escapeHtml(source.locator)}</span>` : ''}
        ${source.section ? `<span class="section">§ ${escapeHtml(source.section)}</span>` : ''}
        <span class="score">${source.score.toFixed(3)}</span>
      </div>
      <div class="excerpt">${escapeHtml(source.excerpt)}</div>
    </div>
  `;
}

// Todos los fragmentos van plegados juntos: el desplegable se abre solo si
// alguien quiere verificar de dónde salió la respuesta.
function renderSources(sources) {
  if (!sources.length) return '';

  return `
    <details class="sources">
      <summary>${sources.length} fragmentos consultados — click para ver</summary>
      <div class="source-list">
        ${sources.map(renderSource).join('')}
      </div>
    </details>
  `;
}

// Llega cuando el texto ya está completo: hasta entonces no se sabe de qué
// fragmentos salió la respuesta. Los citados se marcan donde están, sin sacarlos
// del desplegable.
function applyCitations(answerEl, sources, citedIndices) {
  const details = answerEl.querySelector('details.sources');
  if (!details) return;

  for (const index of citedIndices) {
    details.querySelector(`.source[data-index="${index}"]`)?.classList.add('cited');
  }

  const count = citedIndices.length;
  if (count) {
    details.querySelector('summary').textContent =
      `${count} de ${sources.length} fragmentos sustentan la respuesta — click para ver`;
  }
}

function scrollToBottom() {
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
}

// --- flujo de la consulta ---------------------------------------------------

function ask(question) {
  // Se busca cada vez: `openChat` recrea el estado vacío al cambiar de
  // proyecto, así que una referencia guardada al arrancar apuntaría a un nodo
  // que ya no está en el documento.
  document.getElementById('empty')?.remove();

  const turn = document.createElement('div');
  turn.className = 'turn';
  turn.innerHTML = `
    <div class="question">${escapeHtml(question)}</div>
    <div class="answer streaming">
      <div class="body"></div>
      <div class="thinking">buscando en los documentos…</div>
    </div>
  `;
  conversation.appendChild(turn);
  scrollToBottom();

  const answerEl = turn.querySelector('.answer');
  const bodyEl = turn.querySelector('.body');
  const thinkingEl = turn.querySelector('.thinking');

  sendButton.disabled = true;
  input.disabled = true;

  const source = new EventSource(
    withProject(`/query/stream?question=${encodeURIComponent(question)}`));
  let firstToken = true;
  let sources = [];

  function finish() {
    source.close();
    answerEl.classList.remove('streaming');
    thinkingEl?.remove();
    sendButton.disabled = false;
    input.disabled = false;
    input.focus();
  }

  // Las fuentes llegan antes que el texto: el buscador ya terminó su trabajo
  // cuando el modelo recién empieza a escribir.
  source.addEventListener('sources', (event) => {
    const data = JSON.parse(event.data);
    if (data.insufficient_context) {
      answerEl.classList.add('insufficient');
    }
    // Los scores de preguntas legítimas y ajenas se superponen, así que en la
    // zona dudosa se responde igual pero avisando que hay que verificar.
    if (data.low_confidence) {
      answerEl.insertAdjacentHTML('beforeend',
        '<div class="warning">⚠ Los fragmentos encontrados tienen relevancia dudosa ' +
        'para esta pregunta. Verifica la respuesta contra las fuentes citadas.</div>');
    }
    if (data.sources.length) {
      sources = data.sources;
      answerEl.insertAdjacentHTML('beforeend', renderSources(sources));
    }
    thinkingEl.textContent = 'redactando la respuesta…';
  });

  // Solo se sabe qué fragmentos sustentan la respuesta cuando el texto está
  // completo, así que la atribución llega en su propio evento, después.
  source.addEventListener('cited', (event) => {
    applyCitations(answerEl, sources, JSON.parse(event.data).cited || []);
  });

  source.addEventListener('token', (event) => {
    if (firstToken) {
      thinkingEl.remove();
      firstToken = false;
    }
    bodyEl.textContent += JSON.parse(event.data).text;
    scrollToBottom();
  });

  source.addEventListener('done', finish);

  source.addEventListener('error', (event) => {
    // Puede ser un error del servidor (con data) o un corte de conexión.
    let message = 'Se perdió la conexión con el servidor.';
    if (event.data) {
      try { message = JSON.parse(event.data).message; } catch { /* usar el genérico */ }
    }
    if (!bodyEl.textContent) {
      answerEl.classList.add('failed');
      bodyEl.textContent = message;
    }
    finish();
  });
}

// --- eventos de la interfaz -------------------------------------------------

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const question = input.value.trim();
  if (!question || sendButton.disabled) return;
  input.value = '';
  input.style.height = 'auto';
  ask(question);
});

// Enter envía, Shift+Enter hace salto de línea.
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

// El textarea crece con el contenido, hasta el máximo del CSS.
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = `${input.scrollHeight}px`;
});

// --- integración con la app de escritorio -----------------------------------
//
// Solo se activa dentro de Tauri, que es quien puede abrir el selector de
// carpetas del sistema. Servida desde un navegador, la página no cambia.

const desktop = window.__TAURI__;

// `invoke` cambió de sitio entre versiones de Tauri y, servida por HTTP, la
// interfaz depende de que el global esté inyectado en un origen remoto. Se
// resuelve una sola vez y de forma tolerante: si no aparece, la página se
// comporta como en el navegador en lugar de romperse a mitad de un handler.
const invoke =
  desktop?.core?.invoke ??
  desktop?.invoke ??
  desktop?.tauri?.invoke ??
  null;

/** Si la página corre dentro de la app y puede hablar con la capa nativa. */
const isDesktop = Boolean(invoke);

if (desktop && !invoke) {
  console.warn(
    'Mnemosyne: la app de escritorio no expuso invoke(); ' +
    'los controles nativos quedan desactivados.',
  );
}

/**
 * Abre el selector de carpetas del sistema. Devuelve la ruta elegida, o `null`
 * si el usuario canceló o si la página no corre dentro de la app.
 *
 * Elegir carpeta ya no indexa: el dashboard registra el proyecto primero y la
 * indexación es un paso aparte, con su propio progreso visible.
 */
async function pickFolder() {
  if (!isDesktop) return null;
  return invoke('pick_docs_folder').catch((error) => {
    console.error('Mnemosyne: no se pudo abrir el selector de carpetas:', error);
    return null;
  });
}

// --- asistente de primer arranque -------------------------------------------
//
// Ollama es la única dependencia que no viaja en el instalador: pesa más que
// todo lo demás junto. Si falta él o alguno de los modelos, esto cubre la
// interfaz y guía la instalación en vez de dejar que el chat falle a cada
// pregunta.

const OLLAMA_URL = 'https://ollama.com/download';

async function checkSetup() {
  const panel = document.getElementById('setup');
  const detail = document.getElementById('setup-detail');
  const action = document.getElementById('setup-action');
  const error = document.getElementById('setup-error');

  // El diagnóstico se pide a la API por HTTP y no a la capa nativa: Tauri no
  // inyecta su puente en páginas de origen remoto (tauri-apps/tauri#5088), y
  // esta interfaz la sirve la propia API. Además así la versión web avisa
  // igual, en vez de fallar pregunta a pregunta.
  let status;
  try {
    const response = await fetch('/dependencies');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    status = await response.json();
  } catch (e) {
    // Sin diagnóstico no se puede afirmar que falte nada, así que el panel se
    // deja oculto: bloquear el chat por no haber podido comprobarlo sería peor
    // que dejar que la pregunta falle con su propio mensaje.
    console.error('Mnemosyne: no se pudo comprobar las dependencias:', e);
    panel.hidden = true;
    return;
  }

  if (status.ollama && status.missing_models.length === 0) {
    panel.hidden = true;
    return;
  }

  panel.hidden = false;
  error.hidden = true;

  if (!status.ollama) {
    // Ollama no se puede instalar desde acá sin pedir permisos de
    // administrador, así que se abre su página y se ofrece re-comprobar.
    detail.innerHTML =
      'Mnemosyne necesita <strong>Ollama</strong> para generar las respuestas, ' +
      'y no está corriendo. Es gratuito y se instala una sola vez; después, ' +
      'todo funciona sin conexión.';
    action.textContent = 'Descargar Ollama';
    action.onclick = () => {
      const open = desktop?.opener?.openUrl;
      if (open) open(OLLAMA_URL);
      else window.open(OLLAMA_URL, '_blank');
    };
    return;
  }

  const total = status.missing_models.length;
  detail.innerHTML =
    `Falta descargar ${total === 1 ? 'un modelo' : `${total} modelos`}: ` +
    `<strong>${status.missing_models.join('</strong>, <strong>')}</strong>. ` +
    'Son varios gigabytes y se descargan una sola vez desde Ollama.';
  action.textContent = 'Descargar modelos';
  action.onclick = () => pullModels();
}

async function pullModels() {
  const action = document.getElementById('setup-action');
  const recheck = document.getElementById('setup-recheck');
  const progress = document.getElementById('setup-progress');
  const bar = document.getElementById('setup-bar');
  const label = document.getElementById('setup-status');
  const error = document.getElementById('setup-error');

  action.disabled = recheck.disabled = true;
  action.textContent = 'Descargando…';
  progress.hidden = false;
  error.hidden = true;
  label.textContent = 'preparando la descarga…';

  // La API reenvía el progreso que reporta Ollama. Se lee el flujo a mano y no
  // con EventSource porque este solo hace GET, y la descarga es un POST.
  try {
    const response = await fetch('/dependencies/pull', { method: 'POST' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let failed = null;

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      // El último trozo puede estar cortado a mitad: se guarda para la vuelta
      // siguiente en vez de intentar parsearlo.
      buffer = events.pop() ?? '';

      for (const event of events) {
        const line = event.split('\n').find((l) => l.startsWith('data: '));
        if (!line) continue;

        const payload = JSON.parse(line.slice(6));
        if (payload.error) {
          failed = payload.error;
          continue;
        }
        if (payload.done) continue;

        const { model, status, completed = 0, total = 0 } = payload;
        if (total > 0) {
          bar.style.width = `${Math.round((completed / total) * 100)}%`;
          label.textContent =
            `${model}: ${formatBytes(completed)} de ${formatBytes(total)}`;
        } else {
          label.textContent = `${model}: ${status}`;
        }
      }
    }

    if (failed) throw new Error(failed);

    bar.style.width = '100%';
    label.textContent = 'Listo';
    await checkSetup();
    await refreshStatus();
  } catch (e) {
    error.textContent = String(e);
    error.hidden = false;
    action.textContent = 'Reintentar';
  } finally {
    action.disabled = recheck.disabled = false;
    progress.hidden = true;
    bar.style.width = '0';
  }
}

function formatBytes(bytes) {
  const gb = bytes / 1e9;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`;
}

document.getElementById('setup-recheck')
  .addEventListener('click', () => checkSetup());

// El chat no se carga al arrancar: la vista inicial es el dashboard, y es él
// quien llama a `openChat` cuando se entra en un proyecto.
checkSetup();
