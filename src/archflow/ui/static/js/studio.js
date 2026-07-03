// Studio: view-project gallery + 3-pane editor (outline | draw.io | copilot).

import { api } from './api.js';
import { el, toast, openModal } from './ui.js';

const LAYER_COLORS = {
  motivation: '#CCCCFF', strategy: '#F5DEAA', business: '#FFFF99',
  application: '#99FFFF', technology: '#AFFFAF', implementation: '#FFE0E0', other: '#F5F5F5',
};
const MOTIVATION = new Set(['Stakeholder', 'Driver', 'Assessment', 'Goal', 'Outcome', 'Principle', 'Requirement', 'Constraint', 'Meaning', 'Value']);
const STRATEGY = new Set(['Resource', 'Capability', 'CourseOfAction', 'ValueStream']);
const TECHNOLOGY = new Set(['Node', 'Device', 'SystemSoftware', 'TechnologyService', 'Artifact']);
const IMPLEMENTATION = new Set(['WorkPackage', 'Deliverable', 'Plateau', 'Gap']);

function layerOf(type) {
  if (MOTIVATION.has(type)) return 'motivation';
  if (STRATEGY.has(type)) return 'strategy';
  if (type.startsWith('Business') || ['Contract', 'Representation', 'Product'].includes(type)) return 'business';
  if (type.startsWith('Application') || type === 'DataObject') return 'application';
  if (TECHNOLOGY.has(type)) return 'technology';
  if (IMPLEMENTATION.has(type)) return 'implementation';
  return 'other';
}

// ---- gallery ---------------------------------------------------------------------

export async function renderStudioList(root) {
  root.innerHTML = '';
  let projects;
  try {
    projects = await api.listProjects();
  } catch (error) {
    root.append(el('p', { class: 'empty-note' }, `Could not load projects: ${error.message}`));
    return;
  }

  const grid = el('div', { class: 'studio-list' },
    el('div', { class: 'project-card new', onclick: () => newProjectModal() }, '+ New view project'),
    projects.map((project) => el('div', {
      class: 'project-card', onclick: () => { location.hash = `#/studio/${project.id}`; },
    },
      el('img', {
        class: 'pc-thumb', alt: '', loading: 'lazy',
        src: api.previewUrl(project.id, project.updated_at),
      }),
      el('div', { class: 'pc-name' }, project.name),
      el('div', { class: 'pc-meta' },
        `${project.elements} elements · ${project.relationships} relationships`),
      project.description ? el('div', { class: 'pc-meta' }, project.description) : null,
    )),
  );

  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'Studio — ArchiMate views'),
      el('button', { class: 'btn', onclick: () => importModal() }, 'Import exchange file'),
    ),
    grid,
  );
}

function newProjectModal() {
  const name = el('input', { placeholder: 'e.g. Application landscape' });
  const description = el('input', { placeholder: 'Optional description' });
  const close = openModal(el('div', {},
    el('h2', {}, 'New view project'),
    el('div', { class: 'inline-form' }, name, description),
    el('div', { class: 'modal-actions' },
      el('button', { class: 'btn', onclick: () => close() }, 'Cancel'),
      el('button', {
        class: 'btn btn-primary', onclick: async () => {
          if (!name.value.trim()) return;
          try {
            const project = await api.createProject({ name: name.value.trim(), description: description.value });
            close(); location.hash = `#/studio/${project.id}`;
          } catch (error) { toast(error.message, 'error'); }
        },
      }, 'Create'),
    ),
  ));
}

function importModal() {
  const name = el('input', { placeholder: 'Project name (optional)' });
  const file = el('input', { type: 'file', accept: '.xml,.archimate' });
  const close = openModal(el('div', {},
    el('h2', {}, 'Import ArchiMate Open Exchange file'),
    el('div', { class: 'inline-form' }, name, file),
    el('div', { class: 'modal-actions' },
      el('button', { class: 'btn', onclick: () => close() }, 'Cancel'),
      el('button', {
        class: 'btn btn-primary', onclick: async () => {
          const selected = file.files && file.files[0];
          if (!selected) { toast('Choose a file first', 'error'); return; }
          try {
            const xml = await selected.text();
            const project = await api.importExchange({ name: name.value.trim(), xml });
            close(); location.hash = `#/studio/${project.id}`;
          } catch (error) { toast(error.message, 'error'); }
        },
      }, 'Import'),
    ),
  ));
}

// ---- editor -----------------------------------------------------------------------

export async function renderStudioEditor(root, projectId, config) {
  root.innerHTML = '';
  let project;
  try {
    project = await api.getProject(projectId);
  } catch (error) {
    root.append(el('p', { class: 'empty-note' }, error.message));
    return;
  }

  const outline = el('div', { class: 'studio-side' });
  const saveState = el('span', { class: 'save-state' }, 'Saved');
  const canvasHost = el('div', { class: 'studio-canvas' });
  const editor = createDrawioEmbed(config, projectId, saveState);

  canvasHost.append(
    el('div', { class: 'canvas-bar' },
      el('button', { class: 'btn btn-ghost btn-sm', onclick: () => { location.hash = '#/studio'; } }, '← Projects'),
      el('span', { class: 'title' }, project.name),
      saveState,
      el('a', { class: 'btn btn-sm', href: api.exchangeUrl(projectId) }, 'Export .archimate.xml'),
      el('button', {
        class: 'btn btn-sm', onclick: async () => {
          const xml = await editor.currentXml();
          if (xml === null) { toast('Editor not ready yet', 'error'); return; }
          downloadText(`${project.name}.drawio`, xml);
        },
      }, 'Export .drawio'),
      el('button', { class: 'btn btn-danger btn-sm', onclick: async () => {
        if (!confirm('Delete this view project?')) return;
        await api.deleteProject(projectId);
        location.hash = '#/studio';
      } }, 'Delete'),
    ),
    editor.node,
  );

  const copilot = buildCopilotPanel(project, config, {
    onModelUpdated: async () => {
      await editor.reload();
      renderOutline(outline, await api.getProject(projectId), copilot.sendMessage);
    },
  });

  const shell = el('div', { class: 'studio' }, outline, canvasHost, copilot.panel);
  root.append(shell);
  renderOutline(outline, project, copilot.sendMessage);
  editor.load();
}

async function renderOutline(outlineRoot, project, sendToCopilot) {
  outlineRoot.innerHTML = '';

  // Problems panel: lint findings with a one-click hand-off to the copilot.
  try {
    const findings = await api.lint(project.id);
    const problems = el('div', { class: 'outline-layer' });
    problems.append(el('h3', {}, `Problems (${findings.length})`));
    if (!findings.length) {
      problems.append(el('div', { class: 'empty-note' }, '✓ No ArchiMate issues found'));
    } else {
      const icons = { error: '⛔', warning: '⚠️', info: 'ℹ️' };
      for (const finding of findings.slice(0, 12)) {
        problems.append(el('div', { class: 'outline-el', title: finding.rule },
          `${icons[finding.severity] || ''} ${finding.message}`));
      }
      if (findings.length > 12) {
        problems.append(el('div', { class: 'empty-note' }, `…and ${findings.length - 12} more`));
      }
      if (sendToCopilot) {
        problems.append(el('button', {
          class: 'btn btn-sm', style: 'margin-top:8px',
          onclick: () => sendToCopilot(
            'Fix the problems the linter found:\n' +
            findings.map((f) => `- [${f.severity}] ${f.message}`).join('\n')),
        }, '✨ Fix with copilot'));
      }
    }
    outlineRoot.append(problems);
  } catch { /* lint is advisory; never block the outline */ }

  outlineRoot.append(el('h3', {}, 'Model outline'));
  const byLayer = new Map();
  for (const element of project.model.elements) {
    const layer = layerOf(element.type);
    if (!byLayer.has(layer)) byLayer.set(layer, []);
    byLayer.get(layer).push(element);
  }
  if (byLayer.size === 0) {
    outlineRoot.append(el('p', { class: 'empty-note' }, 'Empty model — ask the copilot to start, or draw in the canvas.'));
  }
  for (const [layer, elements] of byLayer) {
    outlineRoot.append(el('div', { class: 'outline-layer' },
      el('div', { class: 'layer-name' },
        el('span', { class: 'layer-dot', style: `background:${LAYER_COLORS[layer]}` }),
        `${layer} (${elements.length})`),
      elements.map((element) => el('div', { class: 'outline-el', title: element.type },
        `${element.name} · ${element.type}`)),
    ));
  }
  outlineRoot.append(
    el('h3', {}, 'Model'),
    el('div', { class: 'studio-toolbar-side' },
      el('div', { class: 'empty-note' },
        `${project.model.relationships.length} relationships · ${project.model.views.length} view(s)`),
    ),
  );
}

// draw.io embed via the official postMessage JSON protocol.
function createDrawioEmbed(config, projectId, saveState) {
  const origin = config.drawio_embed_url.replace(/\/$/, '');
  const src = `${origin}/?embed=1&proto=json&spin=1&noSaveBtn=0&saveAndExit=0&noExitBtn=1&ui=atlas`;
  const frame = el('iframe', { class: 'drawio-frame', src, title: 'draw.io editor' });
  const previewImg = el('img', {
    class: 'canvas-preview', alt: 'Read-only preview of the current view',
    src: api.previewUrl(projectId, 'fallback'),
  });
  const fallback = el('div', { class: 'canvas-fallback', style: 'display:none' },
    el('div', {},
      previewImg,
      el('p', {}, `Read-only preview — the draw.io editor (${origin}) did not load, likely no network access to it.`),
      el('p', {}, 'The copilot and exports still work; set ARCHFLOW_DRAWIO_EMBED_URL to a reachable (e.g. self-hosted) draw.io.'),
    ));
  const node = el('div', { style: 'display:contents' }, frame, fallback);

  let ready = false;
  let xmlResolvers = [];
  let saveTimer = null;

  const initTimeout = setTimeout(() => {
    if (!ready) { frame.style.display = 'none'; fallback.style.display = 'grid'; }
  }, 12000);

  async function pushCurrentDocument() {
    const { xml } = await api.getDrawio(projectId);
    frame.contentWindow.postMessage(JSON.stringify({ action: 'load', xml, autosave: 1 }), origin);
  }

  async function persist(xml) {
    saveState.textContent = 'Saving…';
    try {
      await api.putDrawio(projectId, xml);
      saveState.textContent = 'Saved';
    } catch (error) {
      saveState.textContent = 'Save failed';
      toast(`Could not save diagram: ${error.message}`, 'error');
    }
  }

  function onMessage(event) {
    if (event.origin !== origin || event.source !== frame.contentWindow) return;
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.event === 'init') {
      ready = true;
      clearTimeout(initTimeout);
      pushCurrentDocument().catch((error) => toast(error.message, 'error'));
    } else if (message.event === 'autosave') {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => persist(message.xml), 900);
    } else if (message.event === 'save') {
      persist(message.xml);
    } else if (message.event === 'export') {
      xmlResolvers.forEach((resolve) => resolve(message.xml || message.data || null));
      xmlResolvers = [];
    }
  }
  window.addEventListener('message', onMessage);

  return {
    node,
    load() { /* iframe src already loading; init message drives the rest */ },
    async reload() {
      if (ready) {
        await pushCurrentDocument();
      } else {
        // Fallback mode: refresh the read-only preview instead.
        previewImg.src = api.previewUrl(projectId, String(Date.now()));
      }
    },
    currentXml() {
      if (!ready) return Promise.resolve(null);
      return new Promise((resolve) => {
        xmlResolvers.push(resolve);
        frame.contentWindow.postMessage(JSON.stringify({ action: 'export', format: 'xml' }), origin);
        setTimeout(() => resolve(null), 3000);
      });
    },
  };
}

function downloadText(filename, text) {
  const anchor = el('a', {
    href: URL.createObjectURL(new Blob([text], { type: 'application/xml' })),
    download: filename,
  });
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}

// ---- copilot ------------------------------------------------------------------------

const SUGGESTIONS = [
  'Model our application landscape for customer management',
  'Create a business process view for order handling',
  'Add a technology layer under the current applications',
  'Review the model for ArchiMate mistakes',
];

function buildCopilotPanel(project, config, { onModelUpdated }) {
  const log = el('div', { class: 'copilot-log' });
  const input = el('textarea', { rows: 2, placeholder: 'Describe what to model…' });
  const send = el('button', { class: 'btn btn-primary' }, 'Send');

  // Replay prior conversation (plain user text + assistant text blocks).
  for (const message of project.assistant_history || []) {
    if (message.role === 'user' && typeof message.content === 'string') {
      log.append(el('div', { class: 'msg user' }, message.content));
    } else if (message.role === 'assistant' && Array.isArray(message.content)) {
      const text = message.content.filter((b) => b.type === 'text').map((b) => b.text).join('\n');
      if (text) log.append(el('div', { class: 'msg assistant' }, text));
    }
  }
  if (!log.children.length) {
    log.append(el('div', { class: 'msg assistant' },
      'Hi! I build ArchiMate views with you — from a blank canvas to a finished diagram. ' +
      'Tell me what you want to model, or pick a suggestion below.'));
  }

  let streaming = null; // AbortController while a turn is running

  async function submit(text) {
    if (streaming) return; // one turn at a time; the button reads Stop now
    const message = (text || input.value).trim();
    if (!message) return;
    input.value = '';
    log.append(el('div', { class: 'msg user' }, message));
    const pending = el('div', { class: 'msg thinking' }, 'Modeling…');
    log.append(pending);
    log.scrollTop = log.scrollHeight;
    streaming = new AbortController();
    send.textContent = 'Stop';

    let final = null;
    let sawError = null;
    let modelTouched = false;
    try {
      await api.copilotStream(project.id, message, (event) => {
        if (event.type === 'round') {
          pending.textContent = `Modeling… (round ${event.round})`;
          if (event.actions.length) {
            log.insertBefore(el('div', { class: 'msg actions' }, event.actions.join(' · ')), pending);
          }
          modelTouched = modelTouched || event.model_updated;
          log.scrollTop = log.scrollHeight;
        } else if (event.type === 'final') {
          final = event;
        } else if (event.type === 'error') {
          sawError = event.message;
        }
      }, streaming.signal);
      pending.remove();
      if (final) {
        log.append(el('div', { class: 'msg assistant' }, final.reply));
        if (final.model_updated) await onModelUpdated();
      } else if (sawError) {
        log.append(el('div', { class: 'msg assistant' }, `⚠️ ${sawError}`));
        if (modelTouched) await onModelUpdated();
      }
    } catch (error) {
      pending.remove();
      if (error.name === 'AbortError') {
        log.append(el('div', { class: 'msg assistant' },
          'Stopped listening — the round in progress finishes on the server; reload to see its result.'));
        if (modelTouched) await onModelUpdated();
      } else {
        log.append(el('div', { class: 'msg assistant' }, `⚠️ ${error.message}`));
      }
    } finally {
      streaming = null;
      send.textContent = 'Send';
      log.scrollTop = log.scrollHeight;
    }
  }

  send.addEventListener('click', () => {
    if (streaming) { streaming.abort(); return; }
    submit();
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit(); }
  });

  const panel = el('div', { class: 'copilot' },
    el('div', { class: 'copilot-head' },
      el('div', { class: 't' }, 'ArchiMate copilot'),
      el('div', { class: 's' }, 'Builds the view A→Z — rendered live in draw.io'),
    ),
  );

  if (!config.assistant_available) {
    panel.append(
      el('div', { class: 'copilot-offline' },
        'The copilot needs an Anthropic API key. Set ', el('code', {}, 'ARCHFLOW_ANTHROPIC_API_KEY'),
        ' in .env and restart. You can still draw manually, import exchange files, and seed maps from requests.'),
      log,
    );
    return { panel, sendMessage: null };
  }

  panel.append(
    log,
    el('div', { class: 'copilot-suggestions' },
      SUGGESTIONS.map((suggestion) =>
        el('button', { class: 'suggestion', onclick: () => submit(suggestion) }, suggestion))),
    el('div', { class: 'copilot-input' }, input, send),
  );
  return { panel, sendMessage: submit };
}
