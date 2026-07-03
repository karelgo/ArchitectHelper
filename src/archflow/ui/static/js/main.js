// App shell: hash router + boot.

import { api } from './api.js';
import { renderBoard, newRequestModal } from './board.js';
import { renderStudioList, renderStudioEditor } from './studio.js';

const root = document.getElementById('view-root');
let config = { drawio_embed_url: 'https://embed.diagrams.net', assistant_available: false };

function setActiveNav(name) {
  document.querySelectorAll('.nav a').forEach((link) => {
    link.classList.toggle('active', link.dataset.nav === name);
  });
}

async function route() {
  // Overlays never survive navigation.
  document.getElementById('drawer-root').innerHTML = '';
  document.getElementById('modal-root').innerHTML = '';
  const hash = location.hash || '#/board';
  const studioMatch = hash.match(/^#\/studio\/(.+)$/);
  if (studioMatch) {
    setActiveNav('studio');
    await renderStudioEditor(root, studioMatch[1], config);
  } else if (hash.startsWith('#/studio')) {
    setActiveNav('studio');
    await renderStudioList(root);
  } else {
    setActiveNav('board');
    await renderBoard(root, config);
  }
}

document.getElementById('new-request-btn').addEventListener('click', () => {
  newRequestModal(() => route());
});
window.addEventListener('hashchange', route);

(async function boot() {
  try {
    config = await api.uiConfig();
  } catch {
    // defaults keep the UI usable; the board will surface API errors itself
  }
  await route();
})();
