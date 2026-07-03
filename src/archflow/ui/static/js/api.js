// Thin fetch client for the ArchFlow API (same origin).

async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (response.status === 204) return null;
  const isJson = (response.headers.get('content-type') || '').includes('application/json');
  const payload = isJson ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = isJson && payload && payload.detail ? payload.detail : response.statusText;
    const error = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    error.status = response.status;
    throw error;
  }
  return payload;
}

export const api = {
  uiConfig: () => request('GET', '/ui-config'),

  // Governance
  listRequests: () => request('GET', '/requests'),
  getRequest: (id) => request('GET', `/requests/${id}`),
  createRequest: (payload) => request('POST', '/requests', payload),
  advance: (id) => request('POST', `/requests/${id}/advance`, { actor: 'ui' }),
  gate: (id) => request('GET', `/requests/${id}/gate`),
  triage: (id, payload) => request('POST', `/requests/${id}/triage`, payload),
  addStakeholder: (id, payload) => request('POST', `/requests/${id}/stakeholders`, payload),
  review: (id, payload) => request('POST', `/requests/${id}/reviews`, payload),
  decide: (id, payload) => request('POST', `/requests/${id}/decisions`, payload),
  reject: (id, payload) => request('POST', `/requests/${id}/reject`, payload),
  events: (id) => request('GET', `/requests/${id}/events`),

  // Studio
  listProjects: () => request('GET', '/studio/views'),
  getProject: (id) => request('GET', `/studio/views/${id}`),
  createProject: (payload) => request('POST', '/studio/views', payload),
  patchProject: (id, payload) => request('PATCH', `/studio/views/${id}`, payload),
  deleteProject: (id) => request('DELETE', `/studio/views/${id}`),
  getDrawio: (id) => request('GET', `/studio/views/${id}/drawio`),
  putDrawio: (id, xml) => request('PUT', `/studio/views/${id}/drawio`, { xml }),
  importExchange: (payload) => request('POST', '/studio/views/import', payload),
  copilot: (id, message) => request('POST', `/studio/views/${id}/assistant`, { message }),
  // Streaming variant: calls onEvent per SSE event ({type: 'round'|'final'|'error'}).
  copilotStream: async (id, message, onEvent, signal) => {
    const response = await fetch(`/studio/views/${id}/assistant/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
      signal,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail || detail; } catch { /* not json */ }
      const error = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      error.status = response.status;
      throw error;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        for (const line of chunk.split('\n')) {
          if (line.startsWith('data: ')) onEvent(JSON.parse(line.slice(6)));
        }
      }
    }
  },
  lint: (id) => request('GET', `/studio/views/${id}/lint`),
  exchangeUrl: (id) => `/studio/views/${id}/exchange`,
  previewUrl: (id, rev) => `/studio/views/${id}/preview.svg?rev=${encodeURIComponent(rev || '')}`,
  mapUrl: (id, rev) => `/requests/${id}/map.svg?rev=${encodeURIComponent(rev || '')}`,

  // Governance assistants (drafts only — humans apply)
  aiStakeholders: (id) => request('POST', `/requests/${id}/assistant/stakeholders`),
  aiApplyStakeholders: (id, proposal, actor = 'ui') =>
    request('POST', `/requests/${id}/assistant/stakeholders/apply`, { proposal, actor }),
  aiPsa: (id, body = {}) => request('POST', `/requests/${id}/assistant/psa`, body),
  aiReview: (id) => request('POST', `/requests/${id}/assistant/review`),
};
