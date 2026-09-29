export class KernelClient {
  async request(path, body) {
    const response = await fetch(path, body === undefined ? {} : { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    let result;
    try { result = await response.json(); } catch { throw new Error(`HTTP ${response.status}: invalid JSON response`); }
    if (!response.ok) {
      const error = new Error(`${result.error?.code ?? 'http.error'}: ${result.error?.message ?? response.status}`);
      error.issue = result.error; throw error;
    }
    return result;
  }
  async capabilities() {
    const result = await this.request('/api/capabilities');
    if (result.api_version !== '0.2.0' || !Array.isArray(result.placeables)) throw new Error('Unsupported kernel API');
    return result;
  }
  validate(project, settings) { return this.request('/api/validate', { project, ...settings }); }
  run(project, settings, request_id) { return this.request('/api/replay', { project, ...settings, request_id }); }
}
