// Small DOM helpers: element builder, toasts, modal, drawer shells.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2), value);
    } else if (value !== undefined && value !== null) {
      node.setAttribute(key, value);
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

export function toast(message, kind = 'info') {
  const root = document.getElementById('toast-root');
  const node = el('div', { class: `toast ${kind === 'error' ? 'error' : ''}` }, message);
  root.append(node);
  setTimeout(() => node.remove(), kind === 'error' ? 6000 : 3200);
}

export function openModal(content) {
  const root = document.getElementById('modal-root');
  const backdrop = el('div', {
    class: 'modal-backdrop',
    onclick: (event) => { if (event.target === backdrop) close(); },
  }, el('div', { class: 'modal' }, content));
  function close() { backdrop.remove(); }
  root.append(backdrop);
  return close;
}

export function openDrawer(content) {
  const root = document.getElementById('drawer-root');
  root.innerHTML = '';
  const backdrop = el('div', { class: 'drawer-backdrop', onclick: close });
  const drawer = el('div', { class: 'drawer' }, content);
  function close() { root.innerHTML = ''; }
  root.append(backdrop, drawer);
  return close;
}

export function stageChip(stage) {
  return el('span', { class: `chip chip-stage-${stage}` }, stage.replaceAll('_', ' '));
}

export function classificationChip(classification) {
  if (!classification) return el('span', { class: 'chip', style: 'background:var(--surface-2);color:var(--muted)' }, 'unclassified');
  return el('span', { class: `chip chip-${classification}` }, classification);
}

export function timeAgo(iso) {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 90) return 'just now';
  const minutes = seconds / 60;
  if (minutes < 90) return `${Math.round(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 36) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
