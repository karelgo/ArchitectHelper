// Governance board: pipeline kanban + request drawer with gate-aware actions.

import { api } from './api.js';
import {
  el, toast, openDrawer, openModal, stageChip, classificationChip,
  timeAgo, initials, invalidate,
} from './ui.js';

const STAGES = [
  'intake', 'triage', 'stakeholder_analysis', 'drafting',
  'peer_review', 'board_approval', 'publication', 'done', 'rejected',
];
const ACTIVE_STAGES = STAGES.slice(0, 7);
const STAGE_LABELS = {
  intake: 'Intake', triage: 'Triage', stakeholder_analysis: 'Stakeholder analysis',
  drafting: 'Drafting', peer_review: 'Peer review', board_approval: 'Board approval',
  publication: 'Publication', done: 'Done', rejected: 'Rejected',
};
// Time-in-stage before a card starts asking for attention.
const AGE_WARM_DAYS = 7;
const AGE_HOT_DAYS = 14;

let assistantOn = false; // set from /ui-config; gates the AI-assist buttons
// Survives re-renders within the session so refreshes don't drop the filter.
const filters = { text: '', classification: '', domain: '' };

function matchesFilters(request) {
  if (filters.classification === 'unclassified' && request.classification) return false;
  if (filters.classification && filters.classification !== 'unclassified'
      && request.classification !== filters.classification) return false;
  if (filters.domain && !request.impacted_domains.includes(filters.domain)) return false;
  if (filters.text) {
    const haystack = [request.title, request.requester, request.owner || '',
      ...request.impacted_domains].join(' ').toLowerCase();
    if (!haystack.includes(filters.text)) return false;
  }
  return true;
}

export async function renderBoard(root, config = null) {
  // Drawer refreshes re-render without config — keep the last known value then.
  if (config !== null) assistantOn = Boolean(config.assistant_available);
  root.innerHTML = '';
  let requests;
  try {
    requests = await api.listRequests();
  } catch (error) {
    root.append(el('p', { class: 'empty-note' }, `Could not load requests: ${error.message}`));
    return;
  }

  if (!requests.length) {
    root.append(emptyBoardState(root));
    return;
  }

  const active = requests.filter((r) => !['done', 'rejected'].includes(r.stage));
  const kpis = el('div', { class: 'kpis' },
    kpi(active.length, 'In flight'),
    kpi(requests.filter((r) => ['peer_review', 'board_approval'].includes(r.stage)).length, 'Awaiting review'),
    kpi(requests.filter((r) => r.stage === 'done').length, 'Published'),
    kpi(requests.filter((r) => r.stage === 'rejected').length, 'Rejected'),
  );

  const board = el('div', { class: 'board' });
  function fillBoard() {
    board.innerHTML = '';
    const visible = requests.filter(matchesFilters);
    for (const stage of ACTIVE_STAGES) {
      const cards = visible.filter((r) => r.stage === stage);
      board.append(
        el('div', { class: 'column' },
          el('div', { class: 'column-head' },
            STAGE_LABELS[stage],
            el('span', { class: 'column-count' }, String(cards.length)),
          ),
          el('div', { class: 'column-body' },
            cards.length === 0 ? el('div', { class: 'empty-note' }, '—') :
            cards.map((request) => requestCard(request)),
          ),
        ),
      );
    }
    const done = visible.filter((r) => r.stage === 'done').length;
    const rejected = visible.filter((r) => r.stage === 'rejected').length;
    board.append(
      el('div', { class: 'column column-archive' },
        el('div', { class: 'column-head' }, 'Archive',
          el('span', { class: 'column-count' }, String(done + rejected))),
        el('div', { class: 'column-body' },
          el('a', { class: 'card', href: '#/archive' },
            el('div', { class: 'card-title' }, 'Done & rejected'),
            el('div', { class: 'card-meta' },
              `${done} published · ${rejected} rejected`),
          ),
        ),
      ),
    );
  }

  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'Governance pipeline'),
    ),
    kpis,
    filterBar(requests, fillBoard),
    board,
  );
  fillBoard();
}

function filterBar(requests, apply) {
  const domains = [...new Set(requests.flatMap((r) => r.impacted_domains))].sort();
  const search = el('input', {
    class: 'filter-search', type: 'search', placeholder: 'Filter by title, requester, owner or domain…',
    value: filters.text,
    oninput: (event) => { filters.text = event.target.value.trim().toLowerCase(); apply(); },
  });
  const classification = el('select', {
    'aria-label': 'Filter by classification',
    onchange: (event) => { filters.classification = event.target.value; apply(); },
  },
    el('option', { value: '' }, 'Any size'),
    ['small', 'medium', 'large', 'unclassified'].map((value) =>
      el('option', { value, selected: filters.classification === value ? '' : undefined }, value)),
  );
  const domain = el('select', {
    'aria-label': 'Filter by domain',
    onchange: (event) => { filters.domain = event.target.value; apply(); },
  },
    el('option', { value: '' }, 'Any domain'),
    domains.map((value) =>
      el('option', { value, selected: filters.domain === value ? '' : undefined }, value)),
  );
  const clear = el('button', {
    class: 'btn btn-ghost btn-sm',
    onclick: () => {
      filters.text = ''; filters.classification = ''; filters.domain = '';
      search.value = ''; classification.value = ''; domain.value = '';
      apply();
    },
  }, 'Clear');
  return el('div', { class: 'filter-bar' }, search, classification, domain, clear);
}

function kpi(value, label) {
  return el('div', { class: 'kpi' },
    el('div', { class: 'kpi-value' }, String(value)),
    el('div', { class: 'kpi-label' }, label),
  );
}

function agingChip(request) {
  if (['done', 'rejected'].includes(request.stage)) return null;
  const enteredAt = request.stage_entered_at || request.updated_at;
  const days = (Date.now() - new Date(enteredAt).getTime()) / 86400000;
  const heat = days > AGE_HOT_DAYS ? ' age-hot' : days > AGE_WARM_DAYS ? ' age-warm' : '';
  return el('span', { class: `age${heat}`, title: 'Time in the current stage' },
    `⏱ ${timeAgo(enteredAt).replace(' ago', '')}`);
}

function requestCard(request) {
  return el('a', { class: 'card', href: `#/request/${request.id}` },
    el('div', { class: 'card-title' }, request.title),
    el('div', { class: 'card-meta' },
      classificationChip(request.classification),
      agingChip(request),
      request.owner
        ? el('span', { class: 'avatar', title: `Owner: ${request.owner}` }, initials(request.owner))
        : null,
      request.requester ? `by ${request.requester}` : null,
    ),
  );
}

function emptyBoardState(root) {
  return el('div', { class: 'empty-state' },
    el('div', { class: 'es-stripe', 'aria-hidden': 'true' }),
    el('h2', {}, 'No requests yet'),
    el('p', {},
      'Register an architecture request and ArchFlow walks it through intake, ',
      'triage, stakeholder analysis, drafting, review, board approval and publication — ',
      'generating the stakeholder map and PSA on the way.'),
    el('div', { class: 'es-actions' },
      el('button', {
        class: 'btn btn-primary',
        onclick: () => newRequestModal(() => renderBoard(root)),
      }, 'Register your first request'),
      el('button', {
        class: 'btn',
        onclick: async () => {
          try {
            await api.createRequest({
              title: 'Example — CRM renewal',
              description: 'The current CRM is end-of-life; evaluate a SaaS replacement.',
              requester: 'you',
              business_goal: 'Higher first-contact resolution',
              impacted_domains: ['crm'],
              stakeholders: [
                { name: 'Sales director', role: 'Sponsor', concerns: ['Adoption', 'Migration'] },
              ],
            });
            toast('Example request created');
            renderBoard(root);
          } catch (error) { toast(error.message, 'error'); }
        },
      }, 'Seed an example to explore'),
    ),
  );
}

export async function renderArchive(root) {
  root.innerHTML = '';
  let requests;
  try {
    requests = await api.listRequests();
  } catch (error) {
    root.append(el('p', { class: 'empty-note' }, `Could not load requests: ${error.message}`));
    return;
  }
  const terminal = requests
    .filter((r) => ['done', 'rejected'].includes(r.stage))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));

  const tableHost = el('div', { class: 'table-scroll' });
  const search = el('input', {
    class: 'filter-search', type: 'search', placeholder: 'Filter the archive…',
    oninput: (event) => fillTable(event.target.value.trim().toLowerCase()),
  });

  function fillTable(text = '') {
    tableHost.innerHTML = '';
    const rows = terminal.filter((r) =>
      !text || [r.title, r.requester, r.owner || ''].join(' ').toLowerCase().includes(text));
    if (!rows.length) {
      tableHost.append(el('p', { class: 'empty-note' },
        terminal.length
          ? 'Nothing matches the filter.'
          : 'Nothing here yet — published and rejected requests land in this archive.'));
      return;
    }
    tableHost.append(el('table', { class: 'archive-table' },
      el('thead', {}, el('tr', {},
        ['Request', 'Outcome', 'Size', 'Requester', 'Owner', 'Closed'].map((h) => el('th', {}, h)))),
      el('tbody', {}, rows.map((request) => el('tr', {
        tabindex: '0', role: 'button',
        onclick: () => openRequestDrawer(request.id, root),
        onkeydown: (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openRequestDrawer(request.id, root);
          }
        },
      },
        el('td', {}, request.title),
        el('td', {}, stageChip(request.stage)),
        el('td', {}, classificationChip(request.classification)),
        el('td', {}, request.requester || '—'),
        el('td', {}, request.owner || '—'),
        el('td', {}, timeAgo(request.updated_at)),
      ))),
    ));
  }

  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'Archive'),
      el('a', { class: 'btn', href: '#/board' }, '← Board'),
    ),
    el('div', { class: 'filter-bar' }, search),
    tableHost,
  );
  fillTable();
}

function copyLink(requestId) {
  const url = `${location.origin}${location.pathname}#/request/${requestId}`;
  const done = () => toast('Link copied');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(done, () => toast(url));
  } else {
    toast(url); // no clipboard access: at least show the link
  }
}

export async function openRequestDrawer(requestId, root) {
  const head = el('div', { class: 'drawer-head' });
  const body = el('div', { class: 'drawer-body' }, el('p', { class: 'empty-note' }, 'Loading…'));
  const close = openDrawer(el('div', { style: 'display:contents' }, head, body), () => {
    // Leaving the drawer restores the board URL without re-rendering.
    if (location.hash.startsWith('#/request/')) history.replaceState(null, '', '#/board');
  });

  // Re-renders in place: the drawer stays open and keeps its scroll position.
  const refresh = async () => { await render(); renderBoard(root); };

  async function render() {
    let request, events, gate, drafts;
    try {
      [request, events, gate, drafts] = await Promise.all([
        api.getRequest(requestId), api.events(requestId),
        api.gate(requestId), api.aiDrafts(requestId),
      ]);
    } catch (error) {
      toast(error.message, 'error');
      close();
      return;
    }
    const scroll = body.scrollTop;
    const terminal = ['done', 'rejected'].includes(request.stage);

    head.innerHTML = '';
    head.append(
      el('button', { class: 'drawer-close', onclick: () => close(), 'aria-label': 'Close' }, '✕'),
      el('div', { class: 'drawer-title' }, request.title),
      el('div', { class: 'drawer-sub' },
        stageChip(request.stage), ' ', classificationChip(request.classification),
        request.owner
          ? el('span', { class: 'avatar', title: `Owner: ${request.owner}` }, initials(request.owner))
          : null,
        request.impacted_domains.length ? ` · ${request.impacted_domains.join(', ')}` : '',
        ' ',
        el('button', { class: 'btn btn-ghost btn-sm', onclick: () => copyLink(requestId) }, '🔗 Copy link'),
      ),
    );

    body.innerHTML = '';

    // --- primary action + live gate ------------------------------------------
    const advanceLabel = terminal ? 'Completed'
      : (gate.ok && gate.next_stage ? `Advance to ${STAGE_LABELS[gate.next_stage]}` : 'Advance stage');
    const gatePanel = terminal ? null : el('div', { class: 'gate-panel' },
      el('h3', {}, gate.next_stage ? `Gate → ${STAGE_LABELS[gate.next_stage]}` : 'Gate'),
      el('ul', { class: 'checklist' },
        gate.conditions.map((c) => el('li', { class: c.met ? 'done' : '' },
          el('span', { class: 'tick' }, c.met ? '[x]' : '[ ]'),
          el('span', {}, c.description),
        ))),
      gate.conditions.length === 0
        ? el('div', { class: 'empty-note' }, 'Nothing to check — this gate is open.') : null,
    );

    body.append(el('div', { class: 'inline-form' },
      el('button', {
        class: gate.ok && !terminal ? 'btn btn-primary' : 'btn',
        disabled: terminal ? '' : undefined,
        onclick: async () => {
          try {
            const result = await api.advance(requestId);
            if (result.advanced) {
              toast(`Advanced to ${STAGE_LABELS[result.to_stage]}`);
              await refresh();
            } else {
              toast('Blocked — the gate lists what is still open', 'error');
              await render();
              const panel = body.querySelector('.gate-panel');
              if (panel) panel.classList.add('flash');
            }
          } catch (error) { toast(error.message, 'error'); }
        },
      }, advanceLabel),
      terminal ? null : el('button', {
        class: 'btn btn-danger',
        onclick: () => rejectForm(requestId, refresh),
      }, 'Reject…'),
      el('a', { class: 'btn', href: '#/studio', onclick: async (event) => {
        event.preventDefault();
        try {
          const project = await api.createProject({ name: '', request_id: requestId });
          location.hash = `#/studio/${project.id}`;
        } catch (error) { toast(error.message, 'error'); }
      } }, 'Open map in Studio'),
    ));
    if (gatePanel) body.append(gatePanel);

    // --- owner ------------------------------------------------------------------
    if (!terminal) {
      body.append(el('h3', {}, 'Owner'), ownerForm(request, refresh));
    }

    // --- AI assist (drafts only — the human applies/records) --------------------
    if (assistantOn && !terminal) {
      const aiRow = el('div', { class: 'inline-form' });
      const lastDraft = (kind, open) => {
        const stored = drafts[kind];
        if (!stored) return null;
        return el('button', {
          class: 'btn btn-ghost btn-sm',
          onclick: () => open(stored.payload),
        }, `View last draft (${timeAgo(stored.created_at)})`);
      };
      const pieces = [];
      if (['intake', 'triage', 'stakeholder_analysis'].includes(request.stage)) {
        pieces.push(
          aiButton('✨ Draft stakeholder analysis', async () => {
            const proposal = await api.aiStakeholders(requestId);
            proposalModal(request, proposal, refresh);
          }),
          lastDraft('stakeholders', (payload) => proposalModal(request, payload, refresh)),
        );
      }
      if (['stakeholder_analysis', 'drafting', 'peer_review'].includes(request.stage)) {
        pieces.push(
          aiButton('✨ Draft PSA', async () => {
            const draft = await api.aiPsa(requestId);
            psaModal(request, draft.markdown, refresh);
          }),
          lastDraft('psa', (payload) => psaModal(request, payload.markdown, refresh)),
        );
      }
      if (['peer_review', 'board_approval'].includes(request.stage)) {
        pieces.push(
          aiButton('✨ AI pre-review', async () => {
            reviewModal(await api.aiReview(requestId));
          }),
          lastDraft('review', (payload) => reviewModal(payload)),
        );
      }
      aiRow.append(...pieces.filter(Boolean));
      if (aiRow.childElementCount) body.append(el('h3', {}, 'AI assist'), aiRow);
    }

    // --- stage-specific forms ---------------------------------------------------
    if (request.stage === 'triage') {
      body.append(el('h3', {}, 'Triage'), triageForm(request, refresh));
    }
    if (['intake', 'triage', 'stakeholder_analysis'].includes(request.stage)) {
      body.append(el('h3', {}, 'Add stakeholder'), stakeholderForm(requestId, refresh));
    }
    if (request.stage === 'peer_review') {
      body.append(el('h3', {}, 'Record review'), reviewForm(requestId, refresh));
    }
    if (['drafting', 'peer_review', 'board_approval'].includes(request.stage)) {
      body.append(el('h3', {}, 'Record decision'), decisionForm(request, refresh));
    }

    // --- facts ---------------------------------------------------------------------
    if (request.stakeholders.length) {
      body.append(el('h3', {}, 'Stakeholders'),
        ...request.stakeholders.map((s) => el('div', { class: 'item-row' },
          el('span', {}, `${s.name}${s.role ? ` — ${s.role}` : ''}`),
          el('span', { style: 'color:var(--muted)' }, s.concerns.join(', ')),
        )),
        el('h3', {}, 'Stakeholder map'),
        el('img', {
          class: 'map-preview', alt: `Stakeholder map for ${request.title}`, loading: 'lazy',
          src: api.mapUrl(requestId, request.updated_at),
        }));
    }

    body.append(el('h3', {}, 'Checklist'), el('ul', { class: 'checklist' },
      request.checklist.map((item) => el('li', { class: item.done ? 'done' : '' },
        el('span', { class: 'tick' }, item.done ? '[x]' : '[ ]'),
        el('span', {}, `${item.description} `,
          el('span', { class: 'stage-tag' }, `(${STAGE_LABELS[item.stage]})`)),
      ))));

    if (request.decisions.length) {
      body.append(el('h3', {}, 'Decisions'),
        ...request.decisions.map((d) => el('div', { class: 'item-row' },
          el('span', {}, d.title),
          el('span', { class: `chip chip-${d.status === 'approved' ? 'small' : d.status === 'rejected' ? 'large' : 'medium'}` }, d.status),
        )));
    }
    if (request.reviews.length) {
      body.append(el('h3', {}, 'Reviews'),
        ...request.reviews.map((r) => el('div', { class: 'item-row' },
          el('span', {}, `${r.reviewer}${r.comments ? ` — ${r.comments}` : ''}`),
          el('span', { class: `chip chip-${r.verdict === 'approve' ? 'small' : 'large'}` }, r.verdict.replaceAll('_', ' ')),
        )));
    }
    if (request.artifacts.length) {
      body.append(el('h3', {}, 'Artifacts'),
        ...request.artifacts.map((a) => el('div', { class: 'item-row' },
          el('span', {}, a.kind.replaceAll('_', ' ')),
          el('span', { class: 'artifact-link' }, a.path),
        )));
    }

    body.append(el('h3', {}, 'Timeline'), el('ul', { class: 'timeline' },
      events.slice().reverse().map((event) => el('li', {},
        el('span', { class: 't-type' }, event.type.replaceAll('_', ' ')),
        ' ', describeEvent(event),
        el('div', { class: 't-actor' }, `${event.actor} · ${new Date(event.occurred_at).toLocaleString()}`),
      ))));

    body.scrollTop = scroll;
  }

  await render();
}

// A button that shows a busy state while its (slow, LLM-backed) action runs.
function aiButton(label, action) {
  const button = el('button', { class: 'btn' }, label);
  button.onclick = async () => {
    button.disabled = true;
    button.textContent = '✨ Thinking…';
    try {
      await action();
    } catch (error) {
      toast(error.message, 'error');
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  };
  return button;
}

function proposalModal(request, proposal, refresh) {
  const picks = []; // { box, kind, item } — unchecked items are not applied
  const section = (title, items, kind, describe) => {
    if (!items.length) return null;
    const rows = items.map((item) => {
      const box = el('input', { type: 'checkbox', checked: '' });
      picks.push({ box, kind, item });
      return el('label', { class: 'item-row', style: 'cursor:pointer' },
        el('span', {}, box, ' ', describe(item)),
      );
    });
    return el('div', {}, el('h3', {}, title), ...rows);
  };

  const close = openModal(el('div', {},
    el('h2', {}, 'Proposed stakeholder analysis'),
    el('div', { class: 'hint' },
      'A draft, not a decision: untick anything that does not belong, then apply.'),
    section('Stakeholders', proposal.stakeholders, 'stakeholders', (s) =>
      `${s.name}${s.role ? ` — ${s.role}` : ''} (influence ${s.influence}, interest ${s.interest}, ${s.attitude})` +
      `${s.concerns.length ? ` · ${s.concerns.join(', ')}` : ''}`),
    section('Drivers', proposal.drivers, 'drivers', (d) => d.name),
    section('Goals', proposal.goals, 'goals', (g) => g.name),
    section('Assessments', proposal.assessments, 'assessments', (a) => a.name),
    proposal.notes ? el('div', { class: 'hint' }, `Assistant notes: ${proposal.notes}`) : null,
    el('div', { class: 'modal-actions' },
      el('button', { class: 'btn', onclick: () => close() }, 'Discard'),
      el('button', {
        class: 'btn btn-primary', onclick: async () => {
          const filtered = { stakeholders: [], drivers: [], goals: [], assessments: [] };
          for (const { box, kind, item } of picks) if (box.checked) filtered[kind].push(item);
          try {
            const added = await api.aiApplyStakeholders(request.id, filtered);
            toast(`Applied: +${added.stakeholders} stakeholders, +${added.drivers} drivers, ` +
              `+${added.goals} goals, +${added.assessments} assessments`);
            close(); refresh();
          } catch (error) { toast(error.message, 'error'); }
        },
      }, 'Apply selection'),
    ),
  ));
}

function psaModal(request, markdown, refresh) {
  const close = openModal(el('div', {},
    el('h2', {}, 'PSA draft'),
    el('pre', { class: 'psa-preview' }, markdown),
    el('div', { class: 'modal-actions' },
      el('button', { class: 'btn', onclick: () => close() }, 'Discard'),
      el('button', {
        class: 'btn btn-primary', onclick: async () => {
          try {
            const saved = await api.aiPsa(request.id, { save: true, markdown });
            toast(`PSA saved to ${saved.saved_path}`);
            close(); refresh();
          } catch (error) { toast(error.message, 'error'); }
        },
      }, 'Save as PSA artifact'),
    ),
  ));
}

function reviewModal(draft) {
  const chip = draft.suggested_verdict === 'approve' ? 'chip-small' : 'chip-large';
  const severityChip = { blocking: 'chip-large', major: 'chip-medium', minor: 'chip-small' };
  const close = openModal(el('div', {},
    el('h2', {}, 'AI pre-review'),
    el('div', { class: 'hint' },
      'Advisory only — nothing is recorded. Use the "Record review" form for the real verdict.'),
    el('p', {},
      el('span', { class: `chip ${chip}` }, draft.suggested_verdict.replaceAll('_', ' ')),
      ` ${draft.summary}`),
    ...draft.findings.map((finding) => el('div', { class: 'item-row' },
      el('span', {}, finding.message),
      el('span', { class: `chip ${severityChip[finding.severity] || 'chip-medium'}` },
        `${finding.severity}${finding.area ? ` · ${finding.area}` : ''}`),
    )),
    el('div', { class: 'modal-actions' },
      el('button', { class: 'btn', onclick: () => close() }, 'Close'),
    ),
  ));
}

function describeEvent(event) {
  const payload = event.payload || {};
  if (payload.stage) return `→ ${payload.stage}`;
  if (payload.reasons) return payload.reasons.join('; ');
  if (payload.name) return payload.name;
  if (payload.title) return payload.title;
  if (payload.kind) return payload.kind;
  return '';
}

function ownerForm(request, refresh) {
  const owner = el('input', {
    placeholder: 'Responsible architect (empty to clear)', value: request.owner || '',
  });
  return el('div', { class: 'inline-form' }, owner,
    el('button', {
      class: 'btn', onclick: async () => {
        try {
          await api.setOwner(request.id, owner.value.trim());
          toast(owner.value.trim() ? `Owner set to ${owner.value.trim()}` : 'Owner cleared');
          refresh();
        } catch (error) { toast(error.message, 'error'); }
      },
    }, 'Assign'));
}

function triageForm(request, refresh) {
  const select = el('select', {},
    ['small', 'medium', 'large'].map((value) =>
      el('option', { value, selected: request.classification === value ? '' : undefined }, value)),
  );
  const domains = el('input', { placeholder: 'Domains, comma separated', value: request.impacted_domains.join(', ') });
  return el('div', { class: 'inline-form' }, select, domains,
    el('button', {
      class: 'btn', onclick: async () => {
        try {
          await api.triage(request.id, {
            classification: select.value,
            impacted_domains: domains.value.split(',').map((d) => d.trim()).filter(Boolean),
          });
          toast('Triage recorded'); refresh();
        } catch (error) { toast(error.message, 'error'); }
      },
    }, 'Save'));
}

function stakeholderForm(requestId, refresh) {
  const name = el('input', { placeholder: 'Name' });
  const role = el('input', { placeholder: 'Role' });
  const concerns = el('input', { placeholder: 'Concerns, | separated' });
  return el('div', { class: 'inline-form' }, name, role, concerns,
    el('button', {
      class: 'btn', onclick: async () => {
        if (!name.value.trim()) { invalidate(name, 'Give the stakeholder a name'); return; }
        try {
          await api.addStakeholder(requestId, {
            name: name.value.trim(), role: role.value.trim(),
            concerns: concerns.value.split('|').map((c) => c.trim()).filter(Boolean),
          });
          toast('Stakeholder added'); refresh();
        } catch (error) { toast(error.message, 'error'); }
      },
    }, 'Add'));
}

function reviewForm(requestId, refresh) {
  const reviewer = el('input', { placeholder: 'Reviewer' });
  const verdict = el('select', {},
    el('option', { value: 'approve' }, 'approve'),
    el('option', { value: 'request_changes' }, 'request changes'));
  const comments = el('input', { placeholder: 'Comments' });
  return el('div', { class: 'inline-form' }, reviewer, verdict, comments,
    el('button', {
      class: 'btn', onclick: async () => {
        if (!reviewer.value.trim()) { invalidate(reviewer, 'Who is reviewing?'); return; }
        try {
          await api.review(requestId, {
            reviewer: reviewer.value.trim(), verdict: verdict.value, comments: comments.value,
          });
          toast('Review recorded'); refresh();
        } catch (error) { toast(error.message, 'error'); }
      },
    }, 'Record'));
}

function decisionForm(request, refresh) {
  const title = el('input', { placeholder: 'Decision title' });
  const status = el('select', {},
    el('option', { value: 'proposed' }, 'proposed'),
    el('option', { value: 'approved', selected: request.stage === 'board_approval' ? '' : undefined }, 'approved'),
    el('option', { value: 'rejected' }, 'rejected'));
  const decidedBy = el('input', { placeholder: 'Decided by' });
  return el('div', { class: 'inline-form' }, title, status, decidedBy,
    el('button', {
      class: 'btn', onclick: async () => {
        if (!title.value.trim()) { invalidate(title, 'A decision needs a title'); return; }
        try {
          await api.decide(request.id, {
            title: title.value.trim(), status: status.value, decided_by: decidedBy.value.trim(),
          });
          toast('Decision recorded'); refresh();
        } catch (error) { toast(error.message, 'error'); }
      },
    }, 'Record'));
}

function rejectForm(requestId, refresh) {
  const reason = el('input', { placeholder: 'Reason' });
  const actor = el('input', { placeholder: 'Your name', value: 'ui' });
  const close = openModal(el('div', {},
    el('h2', {}, 'Reject request'),
    el('div', { class: 'inline-form' }, reason, actor),
    el('div', { class: 'modal-actions' },
      el('button', { class: 'btn', onclick: () => close() }, 'Cancel'),
      el('button', {
        class: 'btn btn-danger', onclick: async () => {
          if (!reason.value.trim()) { invalidate(reason, 'Say why — the audit trail keeps it'); return; }
          try {
            await api.reject(requestId, { actor: actor.value || 'ui', reason: reason.value });
            toast('Request rejected'); close(); refresh();
          } catch (error) { toast(error.message, 'error'); }
        },
      }, 'Reject'),
    ),
  ));
}

export function newRequestModal(onCreated) {
  const fields = {
    title: el('input', { placeholder: 'e.g. CRM renewal' }),
    requester: el('input', { placeholder: 'Who is asking?' }),
    business_goal: el('input', { placeholder: 'What business goal does it serve?' }),
    domains: el('input', { placeholder: 'CRM, Integration' }),
    description: el('textarea', { rows: 3, placeholder: 'What needs to change and why?' }),
    stakeholders: el('textarea', { rows: 3, placeholder: 'One per line: Name : Role : concern | concern' }),
  };
  const close = openModal(el('div', {},
    el('h2', {}, 'New architecture request'),
    el('div', { class: 'form-grid' },
      el('div', { class: 'full' }, el('label', {}, 'Title'), fields.title),
      el('div', {}, el('label', {}, 'Requester'), fields.requester),
      el('div', {}, el('label', {}, 'Impacted domains'), fields.domains),
      el('div', { class: 'full' }, el('label', {}, 'Business goal'), fields.business_goal),
      el('div', { class: 'full' }, el('label', {}, 'Description'), fields.description),
      el('div', { class: 'full' }, el('label', {}, 'Stakeholders'), fields.stakeholders,
        el('div', { class: 'hint' }, 'Format: Name : Role : concern | concern — one stakeholder per line')),
    ),
    el('div', { class: 'modal-actions' },
      el('button', { class: 'btn', onclick: () => close() }, 'Cancel'),
      el('button', {
        class: 'btn btn-primary', onclick: async () => {
          const title = fields.title.value.trim();
          if (!title) { invalidate(fields.title, 'Give the request a title'); return; }
          const stakeholders = fields.stakeholders.value.split('\n')
            .map((line) => line.trim()).filter(Boolean)
            .map((line) => {
              const [name = '', role = '', concernsRaw = ''] = line.split(':').map((p) => p.trim());
              return {
                name, role,
                concerns: concernsRaw.split('|').map((c) => c.trim()).filter(Boolean),
              };
            })
            .filter((s) => s.name);
          try {
            await api.createRequest({
              title,
              requester: fields.requester.value.trim(),
              business_goal: fields.business_goal.value.trim(),
              description: fields.description.value.trim(),
              impacted_domains: fields.domains.value.split(',').map((d) => d.trim()).filter(Boolean),
              stakeholders,
            });
            toast('Request created'); close(); onCreated();
          } catch (error) { toast(error.message, 'error'); }
        },
      }, 'Create'),
    ),
  ));
}
