// Task records outlive editor documents. Only committed server snapshots enter this store.
import { TASK_CONTRACT_VERSION } from './kernel-client.mjs';
import { normalizeReplay } from './replay.mjs';

export const TASK_STORAGE_KEY = 'friskoli.tasks.v1';
export const TERMINAL_TASK_STATES = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
const SERVER_STATES = new Set(['queued', 'running', ...TERMINAL_TASK_STATES]);
const freeze = value => {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(freeze); Object.freeze(value);
  }
  return value;
};
const immutable = value => freeze(structuredClone(value));
const identity = () => globalThis.crypto.randomUUID();
const failure = (code, message = code) => Object.assign(new Error(message), { code });
const isUncertain = error => !error.status || error.status >= 500;
const digestKeys = ['document_sha256', 'scientific_sha256', 'registry_sha256', 'plan_sha256'];
const sameInput = (a, b) => ['document_sha256', 'scientific_sha256', 'registry_sha256', 'plan_sha256', 'edit_revision']
  .every(key => a?.[key] === b?.[key]);

const supportedVersions = new Set([TASK_CONTRACT_VERSION, '0.2.0']);
export function taskCapability(capabilities, project) {
  if (project?.execution_profile === 'conservative-pts-bulk-v1' && project.project_version === '0.3.0') {
    const task = capabilities?.task_profiles?.[project.execution_profile];
    return task?.task_contract_version === '0.2.0' && task.execution?.semantics === project.execution_profile ? task : null;
  }
  if (project?.execution_profile) return null;
  return capabilities?.task?.task_contract_version === TASK_CONTRACT_VERSION ? capabilities.task : null;
}
export function supportsTasks(capabilities, project) {
  return !!taskCapability(capabilities, project);
}

export function buildSubmission(capabilities, project, settings, editRevision, requestId = identity()) {
  const task = taskCapability(capabilities, project);
  if (!task) throw failure('task.unsupported_contract');
  const lock = task.version_lock, execution = task.execution;
  if (!/^[a-f0-9]{64}$/.test(lock?.registry_sha256) || !Array.isArray(lock.implementations) ||
      lock.implementations.some(item => !item.id || !item.version || !/^[a-f0-9]{64}$/.test(item.sha256)) ||
      !execution?.semantics || !execution.backend || !Number.isSafeInteger(execution.default_seed)) {
    throw failure('task.invalid_capabilities');
  }
  const seed = settings.seed ?? execution.default_seed;
  if (!Number.isSafeInteger(seed) || seed < 0) throw failure('task.invalid_seed');
  return immutable({task_contract_version:task.task_contract_version, request_id:requestId,
    edit_revision:String(editRevision), project, version_lock:lock,
    execution:{semantics:execution.semantics, backend:execution.backend, dt_s:settings.dt_s, steps:settings.steps, seed},
    output_plan:{frame_every_steps:1, observables:Object.keys(project.run.channels), include_fields:false}});
}

export function matchesDraft(record, draft) {
  return record.draftToken === draft.draftToken && record.revision === draft.revision;
}

function validateTask(task, record) {
  if (task?.task_contract_version !== record.submission.task_contract_version || !SERVER_STATES.has(task.status) ||
      !/^[A-Za-z0-9_-]{1,128}$/.test(task.run_id) || (record.runId && record.runId !== task.run_id) ||
      !Number.isSafeInteger(task.last_event_seq) || task.last_event_seq < 1 ||
      !Number.isSafeInteger(task.progress?.committed_step) || task.progress.committed_step < 0 ||
      task.progress.committed_step > record.submission.execution.steps ||
      !Number.isFinite(task.progress.simulation_time_s) || task.progress.simulation_time_s < 0 ||
      !['none', 'partial', 'complete'].includes(task.result?.completeness) ||
      (task.status === 'completed') !== (task.result.completeness === 'complete') ||
      digestKeys.some(key => !/^[a-f0-9]{64}$/.test(task.input_snapshot?.[key])) ||
      task.input_snapshot?.edit_revision !== record.submission.edit_revision ||
      task.input_snapshot.registry_sha256 !== record.submission.version_lock.registry_sha256) {
    throw failure('task.invalid_snapshot');
  }
  if (record.task && !sameInput(record.task.input_snapshot, task.input_snapshot)) throw failure('task.input_changed');
}

// Immutable records are replaced, never patched in place; subscribers cannot alter frozen submissions.
export class TaskStore {
  constructor(client, {storage = null, now = Date.now, newId = identity, maxRecords = 12, maxBytes = 2 * 1024 * 1024,
    maxErrors = 3, pollMs = 1000, onChange = () => {}} = {}) {
    this.client = client; this.storage = storage; this.now = now; this.newId = newId;
    this.maxRecords = maxRecords; this.maxBytes = maxBytes; this.maxErrors = maxErrors; this.pollMs = pollMs;
    this.onChange = onChange; this.records = new Map(); this.inflight = new Set(); this.cache = new Map();
    this.timer = null; this.started = false; this.persistenceError = null;
  }
  list() { return [...this.records.values()]; }
  get(id) { return this.records.get(id); }
  storedRecords() {
    return this.list().map(({replay, manifest, ...record}) => {
      const {project, settings, ...compact} = record; return compact;
    });
  }
  persist(strict = false) {
    try {
      const text = JSON.stringify({version:1, records:this.storedRecords()});
      if (new TextEncoder().encode(text).length > this.maxBytes) throw failure('task.storage_limit');
      this.storage?.setItem(TASK_STORAGE_KEY, text); this.persistenceError = null;
    } catch (error) { this.persistenceError = error.message; if (strict) throw error; }
  }
  notify(record) { this.persist(); this.onChange(record, this.list()); }
  replace(id, patch) {
    const current = this.get(id); if (!current) throw failure('task.unknown_local_record');
    if (Object.entries(patch).every(([key, value]) => current[key] === value)) return current;
    const record = freeze({...current, ...immutable(patch)}); this.records.set(id, record); this.notify(record); return record;
  }
  restore() {
    try {
      const saved = JSON.parse(this.storage?.getItem(TASK_STORAGE_KEY) ?? 'null');
      if (!saved || saved.version !== 1 || !Array.isArray(saved.records)) return [];
      if (new TextEncoder().encode(JSON.stringify(saved)).length > this.maxBytes) throw failure('task.storage_limit');
      for (const item of saved.records.slice(-this.maxRecords)) {
        if (!item || typeof item.localId !== 'string' || typeof item.idempotencyKey !== 'string' ||
            !supportedVersions.has(item.submission?.task_contract_version) || !item.submission.project?.run ||
            !Number.isFinite(item.createdAt) || !Number.isSafeInteger(item.cursor) || item.cursor < 0 ||
            !Number.isFinite(item.retryDeadline) || this.records.has(item.localId)) continue;
        const record = immutable({...item, project:item.submission.project, settings:item.submission.execution,
          restored:true, failures:0, paused:false, connection:'recovering', replay:null, manifest:null});
        if (record.task) validateTask(record.task, record);
        this.records.set(record.localId, record);
      }
    } catch (error) { this.persistenceError = error.message; }
    return this.list();
  }
  create(submission, {draftToken, revision, idempotencyRetentionSeconds}) {
    if (this.list().some(r => matchesDraft(r, {draftToken, revision}) &&
        ['submitting', 'submission_unknown'].includes(r.status))) throw failure('task.unresolved_submission');
    while (this.records.size >= this.maxRecords) {
      const old = this.list().find(r => TERMINAL_TASK_STATES.has(r.status) || r.status === 'rejected' || r.status === 'unavailable');
      if (!old) throw failure('task.history_full');
      this.records.delete(old.localId); this.cache.delete(old.localId);
    }
    const localId = `request-${this.newId()}`, createdAt = this.now();
    const retention = Number(idempotencyRetentionSeconds);
    if (!Number.isFinite(retention) || retention <= 0) throw failure('task.invalid_retention');
    const record = immutable({localId, id:localId, runId:null, idempotencyKey:`friskoli-${this.newId()}`,
      submission, project:submission.project, settings:submission.execution, draftToken, revision,
      status:'submitting', completeness:'none', createdAt, retryDeadline:createdAt + retention * 1000,
      cursor:0, eventsComplete:true, task:null, manifest:null, replay:null, failures:0, paused:false,
      connection:'online', error:null, restored:false});
    this.records.set(localId, record);
    try { this.persist(true); } catch (error) { this.records.delete(localId); throw error; }
    this.onChange(record, this.list());
    return record;
  }
  applyTask(id, task) {
    const record = this.get(id); validateTask(task, record);
    if (record.task && task.last_event_seq < record.task.last_event_seq) return record;
    if (record.task && task.last_event_seq === record.task.last_event_seq && JSON.stringify(task) === JSON.stringify(record.task)) {
      return this.replace(id, {connection:'online', error:null});
    }
    if (record.task && TERMINAL_TASK_STATES.has(record.status) && record.status !== task.status) throw failure('task.terminal_changed');
    return this.replace(id, {id:task.run_id, runId:task.run_id, task, status:task.status,
      completeness:task.result.completeness, connection:'online', error:null});
  }
  fault(id, error, submitting = false) {
    const current = this.get(id), failures = current.failures + 1;
    const unavailable = current.runId && [404, 410].includes(error.status);
    const rejected = submitting && !isUncertain(error);
    return this.replace(id, {failures, error:String(error.message).slice(0, 2000),
      status:unavailable ? 'unavailable' : rejected ? 'rejected' : submitting ? 'submission_unknown' : current.status,
      paused:unavailable || rejected || failures >= this.maxErrors, connection:'lost',
      nextAttemptAt:this.now() + Math.max(this.pollMs * 2 ** failures, (error.retryAfter ?? 0) * 1000)});
  }
  async submit(id) {
    if (this.inflight.has(id)) return this.get(id);
    let record = this.get(id);
    if (record.runId) return this.poll(id);
    if (this.now() >= record.retryDeadline) {
      return this.replace(id, {paused:true, error:'task.idempotency_window_expired', connection:'lost', status:'submission_unknown'});
    }
    this.inflight.add(id);
    try {
      // Retrying uses the exact accepted-or-unknown bytes, including the first request_id.
      const task = await this.client.submitTask(record.submission, record.idempotencyKey);
      this.applyTask(id, task); this.replace(id, {failures:0, paused:false});
    } catch (error) { this.fault(id, error, true); }
    finally { this.inflight.delete(id); }
    return this.get(id);
  }
  async events(id) {
    for (let pageIndex = 0; pageIndex < 4; pageIndex++) {
      const record = this.get(id);
      let page;
      try { page = await this.client.taskEvents(record.runId, record.cursor); }
      catch (error) {
        if (error.status !== 410 || error.issue?.code !== 'task.cursor_expired') throw error;
        const task = await this.client.task(record.runId); this.applyTask(id, task);
        this.replace(id, {cursor:task.last_event_seq, eventsComplete:false}); return;
      }
      if (page.run_id !== record.runId || !Array.isArray(page.events) || !Number.isSafeInteger(page.latest_seq) ||
          page.latest_seq < record.cursor || typeof page.has_more !== 'boolean') throw failure('task.invalid_events');
      let cursor = record.cursor;
      for (const event of page.events) {
        if (!Number.isSafeInteger(event.seq)) throw failure('task.event_gap');
        if (event.seq <= cursor) continue;
        if (event.seq !== cursor + 1 || event.task?.last_event_seq !== event.seq) throw failure('task.event_gap');
        validateTask(event.task, record);
        this.applyTask(id, event.task); cursor = event.seq;
      }
      if (page.next_after !== cursor || page.latest_seq < cursor || page.has_more !== (cursor < page.latest_seq) || (page.has_more && cursor === record.cursor)) throw failure('task.invalid_cursor');
      // Save only after the whole page passed validation; a failed page can be safely read again.
      this.replace(id, {cursor});
      if (!page.has_more) return;
    }
  }
  async published(id) {
    let record = this.get(id);
    if (record.completeness === 'none') return;
    const manifest = await this.client.taskResult(record.runId);
    if (manifest?.task_contract_version !== record.submission.task_contract_version || manifest.run_id !== record.runId ||
        !sameInput(manifest.input_snapshot, record.task.input_snapshot) || !Array.isArray(manifest.chunks) || !manifest.chunks.length ||
        !['running', ...TERMINAL_TASK_STATES].includes(manifest.status) || !['partial', 'complete'].includes(manifest.completeness) ||
        !Number.isSafeInteger(manifest.progress?.committed_step) || manifest.progress.committed_step < 0 ||
        (manifest.status === 'completed') !== (manifest.completeness === 'complete')) throw failure('task.invalid_manifest');
    // A publication may race the preceding GET. Refresh the task, then require matching durable progress.
    if (manifest.status !== record.task.status || manifest.progress.committed_step !== record.task.progress.committed_step ||
        manifest.completeness !== record.completeness) {
      this.applyTask(id, await this.client.task(record.runId)); record = this.get(id);
      if (manifest.status !== record.task.status || manifest.progress.committed_step !== record.task.progress.committed_step ||
          manifest.completeness !== record.completeness) return;
    }
    if (record.manifest && JSON.stringify(record.manifest) === JSON.stringify(manifest)) return;
    const cache = this.cache.get(id) ?? new Map(), seenChunks = new Set(), frames = [];
    this.cache.set(id, cache);
    let previousStep = -1;
    for (const chunk of manifest.chunks) {
      if (seenChunks.has(chunk.chunk_id) || !Number.isSafeInteger(chunk.first_step) || !Number.isSafeInteger(chunk.last_step) ||
          chunk.first_step <= previousStep || chunk.last_step < chunk.first_step || chunk.last_step > manifest.progress.committed_step) throw failure('task.invalid_chunk_range');
      seenChunks.add(chunk.chunk_id);
      let cached = cache.get(chunk.chunk_id);
      if (cached && ['chunk_id', 'href', 'sha256', 'bytes', 'media_type', 'first_step', 'last_step']
        .some(key => cached.descriptor[key] !== chunk[key])) throw failure('task.chunk_changed');
      if (!cached) { cached = {descriptor:structuredClone(chunk), body:await this.client.taskChunk(record.runId, chunk)}; cache.set(chunk.chunk_id, cached); }
      const body = cached.body;
      if (body?.task_contract_version !== record.submission.task_contract_version || body.run_id !== record.runId || body.chunk_id !== chunk.chunk_id ||
          !Array.isArray(body.frames) || !body.frames.length || body.frames[0].step_index !== chunk.first_step ||
          body.frames.at(-1).step_index !== chunk.last_step) throw failure('task.invalid_chunk');
      for (const item of body.frames) {
        if (item.sequence !== frames.length || item.step_index !== frames.length || item.frame?.frame_index !== item.step_index ||
            item.time_s !== item.frame.time_s || item.frame.run_id !== record.project.run.run_id ||
            typeof item.grid_revision !== 'string' || !item.grid_revision) throw failure('task.invalid_frame_sequence');
        frames.push(item);
      }
      previousStep = chunk.last_step;
    }
    if (previousStep !== manifest.progress.committed_step || frames.at(-1).time_s !== manifest.progress.simulation_time_s ||
        (manifest.completeness === 'complete' && previousStep !== record.settings.steps)) throw failure('task.incomplete_manifest');
    const replay = normalizeReplay({replay_format_version:'0.1.0', project_id:record.project.id,
      run:record.project.run, domain:record.project.domain,
      execution:{task_contract_version:record.submission.task_contract_version, task_run_id:record.runId, request_id:record.submission.request_id,
        status:manifest.status, completeness:manifest.completeness, include_fields:false, input_snapshot:manifest.input_snapshot},
      snapshots:frames.map(item => ({frame:item.frame, concentrations:{}}))});
    this.replace(id, {manifest, replay});
  }
  async poll(id) {
    if (this.inflight.has(id)) return this.get(id);
    const record = this.get(id);
    if (!record.runId) return this.submit(id);
    this.inflight.add(id);
    try {
      this.applyTask(id, await this.client.task(record.runId));
      await this.events(id); await this.published(id);
      this.replace(id, {failures:0, paused:false, connection:'online', error:null});
    } catch (error) { this.fault(id, error); }
    finally { this.inflight.delete(id); }
    return this.get(id);
  }
  async cancel(id) {
    const record = this.get(id);
    if (!record.runId || !['queued', 'running'].includes(record.status) || record.task?.cancel_requested) return record;
    try { this.applyTask(id, await this.client.cancelTask(record.runId)); }
    catch (error) { this.fault(id, error); }
    return this.get(id);
  }
  async retry(id) {
    this.replace(id, {failures:0, paused:false, nextAttemptAt:0});
    return this.get(id).runId ? this.poll(id) : this.submit(id);
  }
  needsPoll(record) {
    if (record.paused || ['rejected', 'unavailable'].includes(record.status)) return false;
    if (!TERMINAL_TASK_STATES.has(record.status)) return true;
    return record.cursor < record.task.last_event_seq || (record.completeness !== 'none' &&
      (!record.manifest || record.manifest.status !== record.status || record.manifest.completeness !== record.completeness));
  }
  async tick() {
    await Promise.all(this.list().filter(r => this.needsPoll(r) && (r.nextAttemptAt ?? 0) <= this.now())
      .map(r => r.runId ? this.poll(r.localId) : this.submit(r.localId)));
  }
  start() {
    if (this.started) return; this.started = true;
    const loop = async () => { await this.tick(); if (this.started) this.timer = setTimeout(loop, this.pollMs); };
    this.timer = setTimeout(loop, 0);
  }
  stop() { this.started = false; clearTimeout(this.timer); this.timer = null; }
}
