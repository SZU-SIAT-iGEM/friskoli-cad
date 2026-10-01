// These are recorded solver observables. The viewer never estimates arrivals from saved frames.
export const METRIC_KEYS = ['initial_count','live_count','mean_displacement_um','region_fraction','ever_arrived_fraction','mean_residence_s'];
export function validateMetrics(metrics, project) {
  if (metrics?.metric_version !== '0.1.0' || metrics.observation_id !== (project.observation?.id ?? 'whole_domain') ||
      !metrics.by_group || Array.isArray(metrics.by_group) ||
      Object.keys(metrics.by_group).length !== Object.keys(project.groups).length) throw new Error('task.invalid_metrics');
  for (const [id, group] of Object.entries(project.groups)) {
    const values = metrics.by_group[id];
    if (!values || values.initial_count !== group.ids.length || !Number.isSafeInteger(values.live_count) || values.live_count < 0 ||
        METRIC_KEYS.slice(2).some(key => values[key] !== null && !Number.isFinite(values[key])) ||
        ['region_fraction','ever_arrived_fraction'].some(key => values[key] !== null && (values[key] < 0 || values[key] > 1)) ||
        (values.mean_residence_s !== null && values.mean_residence_s < 0)) throw new Error('task.invalid_metrics');
  }
  return metrics;
}

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key,stable(value[key])]));
  return value;
}
const key = value => JSON.stringify(stable(value));
export function comparisonKey(record) {
  return key({observation:record.project.observation,domain:record.project.domain,
    duration_s:record.settings.steps*record.settings.dt_s,dt_s:record.settings.dt_s,
    groups:Object.fromEntries(Object.entries(record.project.groups).map(([id,g]) => [id,g.ids.length]))});
}
export function variantKey(record) {
  const project = structuredClone(record.project);
  delete project.random_seed; delete project.id; delete project.run.run_id;
  return key({project,version_lock:record.submission?.version_lock ?? {kind:'legacy-sync',execution:record.replay?.execution??null},
    backend:record.submission?.execution.backend??'legacy-sync',task_contract:record.submission?.task_contract_version??null});
}
export function compareRuns(records) {
  if (records.length < 2) throw new Error('comparisonNeedTwo');
  if (records.some(r => r.status !== 'completed' || (r.localId && r.completeness !== 'complete') || !r.replay?.snapshots.at(-1)?.metrics)) throw new Error('comparisonCompleteOnly');
  if (records.some(r => comparisonKey(r) !== comparisonKey(records[0]))) throw new Error('comparisonMismatch');
  const variants = new Map();
  for (const record of records) {
    const id = variantKey(record);
    if (!variants.has(id)) variants.set(id,[]);
    variants.get(id).push(record);
  }
  return [...variants.values()].map(runs => {
    const seeds = runs.map(r => r.submission?.execution.seed ?? r.settings.seed ?? r.project.random_seed);
    if (new Set(seeds).size !== seeds.length) throw new Error('comparisonDuplicateSeed');
    const byGroup = {};
    for (const group of Object.keys(runs[0].project.groups)) {
      byGroup[group] = {};
      for (const metric of METRIC_KEYS) {
        const values = runs.map(r => r.replay.snapshots.at(-1).metrics.by_group[group][metric]);
        const mean = values.every(Number.isFinite) ? values.reduce((a,b)=>a+b,0)/values.length : null;
        const sd = mean !== null && values.length > 1 ? Math.sqrt(values.reduce((a,b)=>a+(b-mean)**2,0)/(values.length-1)) : null;
        byGroup[group][metric] = {n:values.filter(Number.isFinite).length,mean,sd};
      }
    }
    const lock=runs[0].submission?.version_lock;
    const provenance=lock?`Task ${runs[0].submission.task_contract_version} · ${runs[0].submission.execution.backend} · registry ${lock.registry_sha256.slice(0,12)}`:'Legacy synchronous result';
    return {label:runs[0].project.id,provenance,versionLock:lock??null,seeds,runs,byGroup};
  });
}

export function metricRows(replay) {
  return replay.snapshots.flatMap(({frame,metrics}) => Object.entries(metrics?.by_group ?? {}).map(([group,values]) =>
    [frame.frame_index,frame.time_s,metrics.observation_id,group,...METRIC_KEYS.map(key => values[key] ?? '')]));
}
export const csvCell = value => /[",\r\n]/.test(String(value)) ? '"'+String(value).replaceAll('"','""')+'"' : String(value);
