// Dashboard: el listado de proyectos y todo lo que se hace desde él.
//
// Vive aparte de app.js porque son dos vistas con poco en común: aquí se
// administra el conjunto de proyectos, allá se conversa con uno. Lo único que
// cruza la frontera son `openChat`, `pickFolder` y `refreshStatus`, definidos
// en app.js, que se carga antes.

const grid = document.getElementById('dash-grid');
const emptyView = document.getElementById('dash-empty');
const listError = document.getElementById('dash-error');

// El último listado recibido, indexado por slug. Los modales trabajan sobre un
// proyecto concreto y necesitan su nombre para poder decirlo por escrito.
let projects = new Map();

// --- utilidades -------------------------------------------------------------

function escape(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Un instante ISO como "hace 3 días". Las fechas exactas no aportan nada aquí:
 * lo que se quiere saber de un vistazo es si el índice está fresco o viejo.
 */
function relativeTime(iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;

  const seconds = Math.max(0, (Date.now() - then.getTime()) / 1000);
  if (seconds < 90) return 'hace un momento';

  const units = [
    [60, 'minuto', 'minutos'],
    [3600, 'hora', 'horas'],
    [86400, 'día', 'días'],
    [2592000, 'mes', 'meses'],
  ];

  let value = seconds;
  let label = ['año', 'años'];
  for (const [size, one, many] of units) {
    const next = seconds / size;
    if (next < 1) break;
    value = next;
    label = [one, many];
  }
  // El último tramo de la tabla es el mes; más allá se cuenta en años.
  if (seconds >= 31536000) {
    value = seconds / 31536000;
    label = ['año', 'años'];
  }

  const rounded = Math.floor(value);
  return `hace ${rounded} ${rounded === 1 ? label[0] : label[1]}`;
}

function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`;
}

// --- listado ----------------------------------------------------------------

async function loadProjects() {
  try {
    const response = await fetch('/projects');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const list = await response.json();

    projects = new Map(list.map((p) => [p.slug, p]));
    listError.hidden = true;
    render(list);
  } catch (error) {
    // Que el listado falle no debe dejar la pantalla en blanco sin explicación:
    // lo normal es que la API todavía esté arrancando.
    listError.textContent = `No se pudo cargar la lista de proyectos: ${error}`;
    listError.hidden = false;
  }
}

function render(list) {
  emptyView.hidden = list.length > 0;
  grid.innerHTML = list.map(card).join('');
}

function card(project) {
  const indexed = project.indexed_at !== null;
  const when = relativeTime(project.indexed_at);

  // Tres estados posibles, y cada uno cambia qué acción tiene sentido ofrecer:
  // sin indexar no hay nada que consultar, y sin carpeta no se puede indexar.
  let badge;
  if (!project.folder_exists) {
    badge = '<span class="badge missing">carpeta no encontrada</span>';
  } else if (indexed) {
    badge = `<span class="badge indexed">${escape(when ?? 'indexado')}</span>`;
  } else {
    badge = '<span class="badge pending">sin indexar</span>';
  }

  let note = '';
  if (!project.folder_exists) {
    note = `<p class="note gone">La carpeta ya no está donde estaba. ${
      indexed
        ? 'Puedes seguir consultando lo ya indexado, pero no re-indexar hasta que vuelva.'
        : 'Vuelve a ponerla en su sitio para poder indexarla.'
    }</p>`;
  } else if (!indexed) {
    note = '<p class="note">Indexa la carpeta para poder hacerle preguntas.</p>';
  }

  // Entrar al chat solo tiene sentido con un índice detrás; sin él la acción
  // principal es indexar, que es lo que falta para que el proyecto sirva.
  const actions = indexed
    ? `<button type="button" class="primary" data-open="${project.slug}">Abrir chat</button>
       <button type="button" class="ghost" data-index="${project.slug}"
         ${project.folder_exists ? '' : 'disabled title="La carpeta no está disponible"'}>
         Re-indexar</button>`
    : `<button type="button" class="primary" data-index="${project.slug}"
         ${project.folder_exists ? '' : 'disabled title="La carpeta no está disponible"'}>
         Indexar</button>`;

  return `
    <article class="card" data-slug="${project.slug}">
      <div class="card-top">
        <div>
          <h3>${escape(project.name)}</h3>
          <p class="path" title="${escape(project.docs_path)}"><bdi>${escape(project.docs_path)}</bdi></p>
        </div>
        ${badge}
      </div>

      ${note}

      <div class="stats">
        <div class="stat">
          <span class="value">${project.documents}</span>
          <span class="label">${project.documents === 1 ? 'documento' : 'documentos'}</span>
        </div>
        <div class="stat">
          <span class="value">${project.chunks}</span>
          <span class="label">${project.chunks === 1 ? 'fragmento' : 'fragmentos'}</span>
        </div>
      </div>

      <div class="actions">
        ${actions}
        <button type="button" class="ghost delete" data-delete="${project.slug}"
          title="Borrar proyecto">Borrar</button>
      </div>
    </article>
  `;
}

// --- entrar en un proyecto --------------------------------------------------

async function enter(slug) {
  // Abrir es lo que reordena el listado, así que se avisa a la API antes de
  // cambiar de vista. Si falla —el proyecto pudo borrarse desde otra ventana—
  // se recarga la lista en vez de entrar a un chat que no responderá.
  try {
    const response = await fetch(`/projects/${encodeURIComponent(slug)}/open`, { method: 'POST' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (error) {
    listError.textContent = `No se pudo abrir el proyecto: ${error}`;
    listError.hidden = false;
    await loadProjects();
    return;
  }

  const project = projects.get(slug);
  // En <bdi> porque el CSS lo recorta por la izquierda invirtiendo la
  // dirección del texto, y sin el aislamiento la ruta se leería al revés.
  document.getElementById('chat-docs').innerHTML =
    project ? `<bdi>${escape(project.docs_path)}</bdi>` : '';
  document.body.dataset.view = 'chat';
  openChat(slug);
}

document.getElementById('back').addEventListener('click', () => {
  document.body.dataset.view = 'dashboard';
  // Los contadores pueden haber cambiado si se re-indexó desde el chat, y el
  // orden con seguridad cambió al entrar.
  loadProjects();
});

// --- indexar ----------------------------------------------------------------

async function index(slug) {
  const cardEl = grid.querySelector(`.card[data-slug="${slug}"]`);
  const actions = cardEl?.querySelector('.actions');
  if (!actions) return;

  // La indexación no reporta porcentaje, así que la barra es indeterminada: no
  // se puede prometer cuánto falta, solo que sigue trabajando.
  actions.outerHTML = `
    <div class="progress">
      <div class="bar"><div class="bar-fill indeterminate"></div></div>
      <p class="label">Indexando documentos… puede tardar varios minutos.</p>
    </div>
  `;

  try {
    const response = await fetch(`/ingest?project=${encodeURIComponent(slug)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail ?? `HTTP ${response.status}`);
    }
  } catch (error) {
    const progress = cardEl.querySelector('.progress .label');
    if (progress) {
      progress.textContent = String(error).replace(/^Error:\s*/, '');
      progress.style.color = 'var(--error)';
    }
    cardEl.querySelector('.bar')?.remove();
    // La tarjeta vuelve a su estado normal tras dar tiempo a leer el error.
    setTimeout(loadProjects, 6000);
    return;
  }

  // Al terminar, el listado trae los contadores nuevos desde el registro.
  await loadProjects();
}

// --- crear ------------------------------------------------------------------

const createModal = document.getElementById('create-modal');
const createForm = document.getElementById('create-form');
const createName = document.getElementById('create-name');
const createPath = document.getElementById('create-path');
const createError = document.getElementById('create-error');
const createSubmit = document.getElementById('create-submit');

function openCreate() {
  createForm.reset();
  createError.hidden = true;
  createModal.hidden = false;
  createName.focus();
}

// Solo la app de escritorio puede abrir el selector del sistema; en el
// navegador la ruta se escribe a mano y el botón no aparece.
if (isDesktop) {
  const browse = document.getElementById('create-browse');
  browse.hidden = false;
  browse.addEventListener('click', async () => {
    const folder = await pickFolder();
    if (folder) {
      createPath.value = folder;
      // Un proyecto sin nombre puede llamarse como su carpeta: es lo que el
      // usuario habría escrito de todas formas, y sigue siendo editable.
      if (!createName.value.trim()) {
        createName.value = folder.split(/[/\\]/).filter(Boolean).pop() ?? '';
      }
    }
  });
}

createForm.addEventListener('submit', async (event) => {
  event.preventDefault();

  const name = createName.value.trim();
  const docsPath = createPath.value.trim();
  if (!name || !docsPath) return;

  createSubmit.disabled = true;
  createError.hidden = true;

  try {
    const response = await fetch('/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, docs_path: docsPath }),
    });

    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      // El 400 más común es una carpeta que no existe, y su mensaje ya explica
      // qué pasó: se muestra tal cual en vez de traducirlo a un genérico.
      throw new Error(detail.detail ?? `HTTP ${response.status}`);
    }

    const project = await response.json();
    createModal.hidden = true;
    await loadProjects();
    // Registrar no indexa, así que se encadena: es lo único que falta para que
    // el proyecto recién creado sirva de algo, y pedirlo dos veces sobra.
    await index(project.slug);
  } catch (error) {
    createError.textContent = String(error).replace(/^Error:\s*/, '');
    createError.hidden = false;
  } finally {
    createSubmit.disabled = false;
  }
});

// --- borrar -----------------------------------------------------------------

const deleteModal = document.getElementById('delete-modal');
const deleteConfirm = document.getElementById('delete-confirm');
let pendingDelete = null;

function confirmDelete(slug) {
  pendingDelete = slug;
  document.getElementById('delete-name').textContent = projects.get(slug)?.name ?? slug;
  deleteModal.hidden = false;
}

deleteConfirm.addEventListener('click', async () => {
  if (!pendingDelete) return;

  deleteConfirm.disabled = true;
  try {
    await fetch(`/projects/${encodeURIComponent(pendingDelete)}`, { method: 'DELETE' });
  } catch (error) {
    listError.textContent = `No se pudo borrar el proyecto: ${error}`;
    listError.hidden = false;
  } finally {
    deleteConfirm.disabled = false;
    deleteModal.hidden = true;
    pendingDelete = null;
    await loadProjects();
  }
});

// --- eventos ----------------------------------------------------------------

// Delegación: las tarjetas se recrean en cada carga del listado, así que un
// listener por botón habría que volver a colgarlo cada vez.
grid.addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button) return;

  if (button.dataset.open) enter(button.dataset.open);
  else if (button.dataset.index) index(button.dataset.index);
  else if (button.dataset.delete) confirmDelete(button.dataset.delete);
});

document.getElementById('new-project').addEventListener('click', openCreate);
emptyView.querySelector('[data-action="new"]').addEventListener('click', openCreate);

for (const modal of [createModal, deleteModal]) {
  modal.addEventListener('click', (event) => {
    // Cierra con el botón de cancelar o al pulsar fuera de la tarjeta, que es
    // lo que espera cualquiera que haya usado un diálogo antes.
    if (event.target === modal || event.target.dataset.action === 'close') {
      modal.hidden = true;
    }
  });
}

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  createModal.hidden = true;
  deleteModal.hidden = true;
});

loadProjects();
