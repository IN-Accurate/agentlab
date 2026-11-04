const base = import.meta.env.VITE_API_URL ?? '';

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.error?.message ?? `Request failed (${status})`);
    this.status = status;
    this.code = body?.error?.code ?? null;
    this.details = body?.error?.details ?? null;
  }
}

function safeParse(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export async function api(path, options = {}) {
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  const text = await response.text();
  const body = safeParse(text);
  if (!response.ok) throw new ApiError(response.status, body);
  return body;
}

export async function proposeFix(resultId, credentialId) {
  const body = JSON.stringify(credentialId ? { credential_id: credentialId } : {});
  const diagnosis = await api(`/v1/run-results/${resultId}/diagnose`, { method: 'POST', body });
  if (!credentialId) return diagnosis;
  try {
    await api(`/v1/diagnoses/${diagnosis.id}/repairs`, { method: 'POST', body });
  } catch (error) {
    // The diagnosis is saved either way, so the review still opens.
    return { ...diagnosis, repairError: error?.message ?? 'Could not propose a repair.', repairErrorCode: error?.code ?? null };
  }
  return diagnosis;
}

export async function streamChat(projectId, body, onEvent, { signal } = {}) {
  const response = await fetch(`${base}/v1/projects/${projectId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, safeParse(text));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, '\n');
    // Hold a trailing CR: it may pair with an LF arriving in the next chunk.
    if (buffer.endsWith('\r')) buffer = `${buffer.slice(0, -1).replace(/\r/g, '\n')}\r`;
    else buffer = buffer.replace(/\r/g, '\n');
    let cut;
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = 'message';
      let data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      onEvent(event, data ? (safeParse(data) ?? {}) : {});
      if (event === 'done') return;
    }
  }
}

export async function pollJob(jobId, { interval = 1000, timeout = 300000 } = {}) {
  const started = Date.now();
  for (;;) {
    const job = await api(`/v1/jobs/${jobId}`);
    if (job.status === 'completed' || job.status === 'failed') return job;
    if (Date.now() - started > timeout) throw new Error('Timed out waiting for job');
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}
