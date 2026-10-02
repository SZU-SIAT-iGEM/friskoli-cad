export const TASK_CONTRACT_VERSION = '0.1.0';

export class KernelClient {
  constructor(fetcher = (...args) => fetch(...args)) { this.fetcher = fetcher; }
  async response(path, body, headers = {}, method) {
    const options = body === undefined ? (method ? {method} : {}) : { method: method ?? 'POST',
      headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) };
    const response = await this.fetcher(path, options);
    if (!response.ok) {
      let result = {};
      try { result = await response.json(); } catch { /* preserve the HTTP status */ }
      const issue = result.issues?.[0] ?? result.error;
      const error = new Error(`${issue?.code ?? 'http.error'}: ${issue?.message ?? response.status}`);
      error.status = response.status; error.issue = issue; error.issues = result.issues;
      const retryAfter = Number(response.headers?.get?.('Retry-After'));
      if (Number.isFinite(retryAfter) && retryAfter > 0) error.retryAfter = retryAfter;
      throw error;
    }
    return response;
  }
  async request(path, body, headers, method) {
    const response = await this.response(path, body, headers, method);
    try { return await response.json(); } catch { throw new Error(`HTTP ${response.status}: invalid JSON response`); }
  }
  async capabilities() {
    const result = await this.request('/api/capabilities');
    if (result.api_version !== '0.2.0' || !Array.isArray(result.placeables)) throw new Error('Unsupported kernel API');
    return result;
  }
  validate(project, settings) { return this.request('/api/validate', { project, dt_s:settings.dt_s,steps:settings.steps }); }
  preflight(submission) { return this.request('/api/runs/preflight', submission); }
  run(project, settings, request_id) { return this.request('/api/replay', { project,dt_s:settings.dt_s,steps:settings.steps, request_id }); }
  submitTask(submission, key) {
    if (!key) throw new Error('Idempotency-Key required');
    return this.request('/api/runs', submission, { 'Idempotency-Key': key });
  }
  task(id) { return this.request(`/api/runs/${encodeURIComponent(id)}`); }
  taskEvents(id, after) { return this.request(`/api/runs/${encodeURIComponent(id)}/events?after=${after}`); }
  cancelTask(id) { return this.request(`/api/runs/${encodeURIComponent(id)}/cancel`, undefined, undefined, 'POST'); }
  taskResult(id) { return this.request(`/api/runs/${encodeURIComponent(id)}/result`); }
  async taskChunk(id, chunk) {
    const path = `/api/runs/${encodeURIComponent(id)}/chunks/${encodeURIComponent(chunk.chunk_id)}`;
    if (chunk.href !== path || chunk.media_type !== 'application/json') throw new Error('Invalid published chunk path');
    const response = await this.response(path);
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength !== chunk.bytes) throw new Error('Published chunk byte length mismatch');
    const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    const hex = Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join('');
    if (hex !== chunk.sha256) throw new Error('Published chunk checksum mismatch');
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  }
}
