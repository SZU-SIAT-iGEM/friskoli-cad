import {LinkedReplay} from './paged-replay.mjs';
// Task records outlive editor documents. Only committed server snapshots enter this store.
import { TASK_CONTRACT_VERSION } from './kernel-client.mjs';
import { normalizeReplay } from './replay.mjs';
import { validateMetrics } from './metrics.mjs';
import {fieldDisplayDomain} from './field-slice.mjs';

export const TASK_STORAGE_KEY = 'friskoli.tasks.v1';
export const TERMINAL_TASK_STATES = new Set(['completed', 'failed', 'cancelled', 'interrupted', 'paused']);
const SERVER_STATES = new Set(['queued', 'running', ...TERMINAL_TASK_STATES]);
const freeze = value => {
  if(ArrayBuffer.isView(value))return value;
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

const supportedVersions = new Set([TASK_CONTRACT_VERSION]);
export function taskCapability(capabilities, project) {
  if(project?.project_version!=='0.6.0'||project.execution_profile!=='modular-spatial-v1')return null;
  const task=capabilities?.task_longrun_profiles?.[project.execution_profile]??capabilities?.task_profiles?.[project.execution_profile]??capabilities?.task;
  return task?.task_contract_version==='0.6.0'&&task.execution?.semantics==='modular-spatial-v1'?task:null;
}
export function supportsTasks(capabilities, project) {
  return !!taskCapability(capabilities, project);
}

export function executionStepLimit(capabilities, project) {
  const limits = [taskCapability(capabilities, project)?.limits?.steps, capabilities?.limits?.steps];
  return limits.find(value => Number.isSafeInteger(value) && value > 0) ?? 100;
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
  const spatial = project.execution_profile==='modular-spatial-v1';
  const seed = settings.seed ?? (spatial ? project.random_seed : execution.default_seed);
  if (spatial && settings.include_fields !== undefined && typeof settings.include_fields !== 'boolean') throw failure('task.invalid_output_plan');
  if (!Number.isSafeInteger(seed) || seed < 0) throw failure('task.invalid_seed');
  const stride = task.task_contract_version==='0.6.0' ? (settings.frame_every_steps ?? 1) : 1;
  if (!Number.isSafeInteger(stride) || stride < 1 || stride > (task.task_contract_version==='0.6.0'?4320000:10000)) throw failure('task.invalid_output_plan');
  const modern=task.task_contract_version==='0.6.0',backend=settings.backend??execution.backend;
  if(!(modern?execution.available_backends??[execution.backend]:[execution.backend]).includes(backend))throw failure('task.unsupported_backend');
  const fieldStride=settings.field_stride_xyz??[1,1,1];
  if(modern&&(!Array.isArray(fieldStride)||fieldStride.length!==3||fieldStride.some((s,i)=>!Number.isSafeInteger(s)||s<1||project.domain.counts_xyz[i]%s!==0)))throw failure('task.invalid_field_stride');
  if(modern&&settings.include_final_fields!==undefined&&typeof settings.include_final_fields!=='boolean')throw failure('task.invalid_output_plan');
  return immutable({task_contract_version:task.task_contract_version, request_id:requestId,
    edit_revision:String(editRevision), project, version_lock:lock,
    execution:{semantics:execution.semantics, backend, dt_s:settings.dt_s, steps:settings.steps, seed},
    output_plan:{frame_every_steps:stride, observables:Object.keys(project.run.channels), include_fields:spatial ? (settings.include_fields ?? true) : false,
      ...(modern?{field_stride_xyz:fieldStride,include_final_fields:settings.include_final_fields??false}:{})}});
}

export async function preflightSubmission(client, capabilities, project, settings, editRevision) {
  const submission = buildSubmission(capabilities, project, settings, editRevision);
  const result = await client.preflight(submission);
  if (result?.valid !== true) throw failure('task.invalid_preflight');
  return {submission, result};
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
  constructor(client, {storage = null, now = Date.now, newId = identity, maxRecords = 64, maxBytes = 16 * 1024 * 1024,
    maxErrors = 3, pollMs = 1000, onChange = () => {}, isProtected = () => false} = {}) {
    this.client = client; this.storage = storage; this.now = now; this.newId = newId;
    this.maxRecords = maxRecords; this.maxBytes = maxBytes; this.maxErrors = maxErrors; this.pollMs = pollMs;
    this.onChange = onChange; this.isProtected = isProtected; this.records = new Map(); this.inflight = new Set(); this.cache = new Map();this.pagers=new Map();
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
      const write=this.storage?.setItem(TASK_STORAGE_KEY, text); this.persistenceError = null;
      if(write?.then)write.catch(error=>{this.persistenceError=error.message;});
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
      if(saved.records.length>this.maxRecords)throw failure('task.history_full');
      for (const item of saved.records) {
        if (!item || typeof item.localId !== 'string' || typeof item.idempotencyKey !== 'string' ||
            !supportedVersions.has(item.submission?.task_contract_version) || !item.submission.project?.run ||
            !Number.isFinite(item.createdAt) || !Number.isSafeInteger(item.cursor) || item.cursor < 0 ||
            !Number.isFinite(item.retryDeadline) || this.records.has(item.localId)) continue;
        const record = immutable({...item, project:item.submission.project, settings:{...item.submission.execution,...item.submission.output_plan},
          restored:true, failures:0, paused:false, connection:'recovering', replay:null, manifest:null});
        if (record.task) validateTask(record.task, record);
        this.records.set(record.localId, record);
      }
    } catch (error) { this.persistenceError = error.message; }
    return this.list();
  }
  evictionPlan(count = 1) {
    if (!Number.isSafeInteger(count) || count < 1 || count > this.maxRecords) throw failure('task.history_full');
    const needed = Math.max(0, this.records.size + count - this.maxRecords);
    const candidates = this.list().filter(r => !this.isProtected(r) &&
      (TERMINAL_TASK_STATES.has(r.status) || r.status === 'rejected' || r.status === 'unavailable')).slice(0, needed);
    if (candidates.length !== needed) throw failure('task.history_full');
    return candidates;
  }
  assertCapacity(count = 1) { this.evictionPlan(count); }
  assertBatchCapacity(submissions) {
    this.assertCapacity(submissions.length);
    const projected={version:1,records:[...this.storedRecords(),...submissions.map(submission=>({submission,
      localId:'request-'+ '0'.repeat(36),idempotencyKey:'friskoli-'+ '0'.repeat(36),design_ref:{design_id:'0'.repeat(128),candidate_id:'0'.repeat(128),candidate_name:'0'.repeat(256)}}))]};
    // Reserve 8 KiB per record for subsequent task status/locks/events cursor metadata.
    const projectedBytes=new TextEncoder().encode(JSON.stringify(projected)).length+8192*(this.records.size+submissions.length);
    if(projectedBytes>this.maxBytes)throw failure('task.storage_limit');
    return {records:this.records.size+submissions.length,bytes:projectedBytes};
  }
  async flushPersistence() {
    try { await this.storage?.flush?.();this.persistenceError=null; }
    catch(error){this.persistenceError=error.message;throw error;}
  }
  create(submission, {draftToken, revision, idempotencyRetentionSeconds, design_ref}) {
    if (this.list().some(r => matchesDraft(r, {draftToken, revision}) &&
        ['submitting', 'submission_unknown'].includes(r.status))) throw failure('task.unresolved_submission');
    if (design_ref && !['design_id','candidate_id','candidate_name'].every(key => typeof design_ref[key] === 'string' && design_ref[key])) throw failure('task.invalid_design_ref');
    const evicted = this.evictionPlan();
    const localId = `request-${this.newId()}`, createdAt = this.now();
    const retention = Number(idempotencyRetentionSeconds);
    if (!Number.isFinite(retention) || retention <= 0) throw failure('task.invalid_retention');
    const record = immutable({localId, id:localId, runId:null, idempotencyKey:`friskoli-${this.newId()}`,
      submission, ...(design_ref ? {design_ref} : {}), project:submission.project, settings:{...submission.execution,...submission.output_plan}, draftToken, revision,
      status:'submitting', completeness:'none', createdAt, retryDeadline:createdAt + retention * 1000,
      cursor:0, eventsComplete:true, task:null, manifest:null, replay:null, failures:0, paused:false,
      connection:'online', error:null, restored:false});
    const previous = this.records;
    this.records = new Map(previous);
    for (const old of evicted) this.records.delete(old.localId);
    this.records.set(localId, record);
    try { this.persist(true); } catch (error) { this.records = previous; throw error; }
    for (const old of evicted) this.cache.delete(old.localId);
    this.onChange(record, this.list());
    return record;
  }
  removeDesignRecords(designId) {
    const selected=this.list().filter(r=>r.design_ref?.design_id===designId);
    if(selected.some(r=>!TERMINAL_TASK_STATES.has(r.status)&&!['rejected','unavailable'].includes(r.status)) || selected.some(r=>this.inflight.has(r.localId)))throw failure('task.design_active');
    const previous=this.records;this.records=new Map(previous);
    for(const r of selected)this.records.delete(r.localId);
    try{this.persist(true);}catch(error){this.records=previous;throw error;}
    for(const r of selected)this.cache.delete(r.localId);
    return this.list();
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
    let sent=false;
    try {
      // An accepted-or-unknown task must never reach the server before its exact
      // request bytes and idempotency key are committed to durable storage.
      if(this.storage?.flush)await this.flushPersistence();
      // Retrying uses the exact accepted-or-unknown bytes, including the first request_id.
      sent=true;
      const task = record.operation?await this.client.request(record.operation.path,record.operation.body,{'Idempotency-Key':record.idempotencyKey}):await this.client.submitTask(record.submission, record.idempotencyKey);
      this.applyTask(id, task.task??task); this.replace(id, {failures:0, paused:false});
    } catch (error) { this.fault(id, error, true);if(!sent)this.persistenceError=error.message; }
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
    if(record.submission.task_contract_version!=='0.6.0')throw failure('task.unsupported_contract');
    const pager=await LinkedReplay.create(this.client,record,manifest),index=pager.frameCount-1,frame=await pager.frame(index);
    const history=await pager.metricHistory(record.replay?.metric_snapshots??[]);pager.metricSnapshots=history;
    this.releaseOtherPagers(id);this.pagers.set(id,pager);
    this.replace(id,{manifest,replay:{...pager.replay({...frame,concentrations:{}},index),metric_snapshots:history}});
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
  releaseOtherPagers(id){for(const [key,pager]of this.pagers)if(key!==id){pager.clear();}}
  async frame(id,index){this.releaseOtherPagers(id);const pager=this.pagers.get(id);if(!pager)throw failure('task.no_paged_result');const snapshot=await pager.frame(index);return pager.replay(snapshot,index);}
  async startOperation(submission,context,path,body){const record=this.create(submission,context);this.replace(record.localId,{operation:{path,body}});await this.submit(record.localId);return this.get(record.localId);}
  async pauseTask(id){const record=this.get(id);try{this.applyTask(id,await this.client.pauseTask(record.runId));}catch(error){this.fault(id,error);}return this.get(id);}
  async resumeTask(id){const original=this.get(id),requestId=this.newId(),editRevision=original.submission.edit_revision;
    const submission={...structuredClone(original.submission),request_id:requestId};
    const record=this.create(submission,{draftToken:original.draftToken,revision:original.revision,idempotencyRetentionSeconds:Math.max(60,(original.retryDeadline-original.createdAt)/1000),design_ref:original.design_ref});
    this.replace(record.localId,{operation:{path:`/api/runs/${encodeURIComponent(original.runId)}/resume`,body:{request_id:requestId,edit_revision:editRevision}}});await this.submit(record.localId);return this.get(record.localId);
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
