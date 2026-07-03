// Small DOM helpers: element builder, toasts, modal, drawer shells, theme.

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
  // Placeholder-only inputs still need an accessible name.
  if (attrs.placeholder && !attrs['aria-label']) node.setAttribute('aria-label', attrs.placeholder);
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

// Inline field validation: mark the input, say what's wrong, clear on typing.
export function invalidate(input, message) {
  input.classList.add('invalid');
  input.setAttribute('aria-invalid', 'true');
  let note = input.nextElementSibling;
  if (!note || !note.classList.contains('field-error')) {
    note = el('div', { class: 'field-error', role: 'alert' }, message);
    input.insertAdjacentElement('afterend', note);
    input.addEventListener('input', () => {
      input.classList.remove('invalid');
      input.removeAttribute('aria-invalid');
      note.remove();
    }, { once: true });
  } else {
    note.textContent = message;
  }
  input.focus();
  return false;
}

const FOCUSABLE = 'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])';

function trapFocus(container) {
  container.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    const focusables = [...container.querySelectorAll(FOCUSABLE)]
      .filter((node) => node.offsetParent !== null);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
}

export function openModal(content, label = 'Dialog') {
  const root = document.getElementById('modal-root');
  const opener = document.activeElement;
  const dialog = el('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': label }, content);
  const backdrop = el('div', {
    class: 'modal-backdrop',
    onclick: (event) => { if (event.target === backdrop) close(); },
  }, dialog);
  function onKey(event) { if (event.key === 'Escape') close(); }
  function close() {
    document.removeEventListener('keydown', onKey);
    backdrop.remove();
    if (opener && opener.focus) opener.focus();
  }
  document.addEventListener('keydown', onKey);
  trapFocus(dialog);
  root.append(backdrop);
  const first = dialog.querySelector('input, select, textarea, button');
  if (first) first.focus();
  return close;
}

export function openDrawer(content, onClose, label = 'Details') {
  const root = document.getElementById('drawer-root');
  root.innerHTML = '';
  const opener = document.activeElement;
  const backdrop = el('div', { class: 'drawer-backdrop', onclick: close });
  const drawer = el('div', {
    class: 'drawer', role: 'dialog', 'aria-modal': 'true', 'aria-label': label,
  }, content);
  function onKey(event) {
    // A modal stacked on top of the drawer owns Escape until it closes.
    if (event.key === 'Escape' && !document.querySelector('.modal-backdrop')) close();
  }
  function close() {
    document.removeEventListener('keydown', onKey);
    if (!root.children.length) return; // already closed (e.g. route change)
    root.innerHTML = '';
    if (opener && opener.focus) opener.focus();
    if (onClose) onClose();
  }
  document.addEventListener('keydown', onKey);
  trapFocus(drawer);
  root.append(backdrop, drawer);
  const closeButton = drawer.querySelector('.drawer-close');
  if (closeButton) closeButton.focus();
  return close;
}

// ---- theme ----------------------------------------------------------------------

export function currentTheme() {
  const forced = document.documentElement.dataset.theme;
  if (forced) return forced;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function toggleTheme() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('archflow-theme', next);
  return next;
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

export function initials(name) {
  return name.split(/[\s.\-_]+/).filter(Boolean).map((part) => part[0]).join('')
    .slice(0, 2).toUpperCase();
}
