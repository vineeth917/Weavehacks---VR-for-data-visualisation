const WS_URL = 'ws://localhost:8080/ws';
const SESSION_ID = 's1';
const RECONNECT_DELAY_MS = 3000;

const handlers = {};
let ws = null;
let reconnectTimer = null;
let mockResponses = null;

const mockMode = new URLSearchParams(window.location.search).get('mock') === '1';

function dispatchMessage(msg) {
  console.log(`WebSocket message received: ${msg.type}`, msg);
  const handler = handlers[msg.type];
  if (handler) {
    handler(msg);
  }
}

export function onMessage(type, fn) {
  handlers[type] = fn;
}

function send(payload) {
  if (mockMode) {
    console.log('Mock mode: message not sent', payload);
    return;
  }
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  } else {
    console.warn('WebSocket not connected, cannot send message', payload);
  }
}

export function sendQuery(text) {
  send({
    type: 'voice_query',
    text,
    session_id: SESSION_ID,
  });
}

export function sendCommand(action, params = {}) {
  send({
    type: 'command',
    action,
    params,
    session_id: SESSION_ID,
  });
}

export function sendInteraction(action, fields = {}) {
  const payload = {
    type: 'interaction',
    action,
    session_id: SESSION_ID,
    ...fields,
  };

  send(payload);

  if (mockMode) {
    simulateInteractionResponse(payload);
  }
}

function substitute(template, vars) {
  if (Array.isArray(template)) {
    return template.map((item) => substitute(item, vars));
  }
  if (template && typeof template === 'object') {
    const result = {};
    for (const [key, value] of Object.entries(template)) {
      result[key] = substitute(value, vars);
    }
    return result;
  }
  if (typeof template === 'string') {
    const fullMatch = template.match(/^\{(\w+)\}$/);
    if (fullMatch) {
      const value = vars[fullMatch[1]];
      if (value !== undefined) return value;
    }
    return template.replace(/\{(\w+)\}/g, (_, key) => {
      const value = vars[key];
      return value !== undefined ? String(value) : `{${key}}`;
    });
  }
  return template;
}

async function simulateInteractionResponse(payload) {
  if (!mockResponses) {
    try {
      const resp = await fetch('/mocks/interaction_responses.json');
      mockResponses = await resp.json();
    } catch (err) {
      console.error('Failed to load interaction_responses mock', err);
      return;
    }
  }

  const { action, target_id, point_ids } = payload;
  const templateKey = action === 'select_point' && target_id && !target_id.startsWith('r')
    ? 'select_panel'
    : action;

  const sequence = mockResponses[templateKey] || mockResponses[action];
  if (!sequence) return;

  const vars = {
    target_id: target_id ?? '',
    point_ids: point_ids ?? [],
    count: point_ids?.length ?? 0,
  };

  for (let i = 0; i < sequence.length; i++) {
    if (i > 0) {
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
    const msg = substitute(sequence[i], vars);
    dispatchMessage(msg);
  }
}

function scheduleReconnect() {
  if (mockMode || reconnectTimer) {
    return;
  }
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, RECONNECT_DELAY_MS);
}

function connect() {
  ws = new WebSocket(WS_URL);

  ws.addEventListener('open', () => {
    console.log('WebSocket connected');
  });

  ws.addEventListener('message', (event) => {
    try {
      const msg = JSON.parse(event.data);
      dispatchMessage(msg);
    } catch (err) {
      console.error('Failed to parse WebSocket message', err, event.data);
    }
  });

  ws.addEventListener('close', () => {
    console.log('WebSocket disconnected');
    scheduleReconnect();
  });

  ws.addEventListener('error', (err) => {
    console.error('WebSocket error', err);
  });
}

function normalizeMessage(data, type) {
  return typeof data.type === 'string' ? data : { type, ...data };
}

async function runMockMode() {
  console.log('Mock mode enabled — skipping WebSocket connection');

  setTimeout(async () => {
    try {
      const resp = await fetch('/mocks/panels.json');
      const data = await resp.json();
      dispatchMessage(normalizeMessage(data, 'panels'));
    } catch (err) {
      console.error('Failed to load panels mock', err);
    }
  }, 1000);

  setTimeout(async () => {
    try {
      const resp = await fetch('/mocks/scatter3d.json');
      const data = await resp.json();
      dispatchMessage(normalizeMessage(data, 'scatter3d'));
    } catch (err) {
      console.error('Failed to load scatter3d mock', err);
    }
  }, 2000);

  setTimeout(async () => {
    try {
      const resp = await fetch('/mocks/training_updates.json');
      const data = await resp.json();
      const items = Array.isArray(data) ? data : data.updates ?? [data];

      for (let i = 0; i < items.length; i++) {
        if (i > 0) {
          await new Promise((resolve) => setTimeout(resolve, 150));
        }
        dispatchMessage(normalizeMessage(items[i], 'training_update'));
      }
    } catch (err) {
      console.error('Failed to load training_updates mock', err);
    }
  }, 3000);

  setTimeout(async () => {
    try {
      const resp = await fetch('/mocks/kde_surface.json');
      const data = await resp.json();
      dispatchMessage(normalizeMessage(data, 'kde_surface'));
    } catch (err) {
      console.error('Failed to load kde_surface mock', err);
    }
  }, 5000);

  setTimeout(async () => {
    try {
      const resp = await fetch('/mocks/corr_field.json');
      const data = await resp.json();
      dispatchMessage(normalizeMessage(data, 'corr_field'));
    } catch (err) {
      console.error('Failed to load corr_field mock', err);
    }
  }, 6000);
}

if (mockMode) {
  runMockMode();
} else {
  connect();
}
