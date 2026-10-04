import {meanInterval} from './result-analysis.mjs';
// These are recorded solver observables. The viewer never estimates arrivals from saved frames.
export const METRIC_KEYS = ['initial_count','live_count','mean_position_um','drift_um_s','mean_displacement_um','region_fraction','ever_arrived_fraction','mean_residence_s','cumulative_degradation_molecules','degradation_per_initial_cell_molecules'];
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
    if(project.observation?.radial_center_um){
      const radial=values.radial,radii=project.observation.radial_radii_um,finiteOrNull=v=>v===null||Number.isFinite(v),nonnegative=v=>finiteOrNull(v)&&(v===null||v>=0),fraction=v=>nonnegative(v)&&(v===null||v<=1);
      if(!radial||JSON.stringify(radial.center_um)!==JSON.stringify(project.observation.radial_center_um)||!nonnegative(radial.mean_distance_um)||!finiteOrNull(radial.mean_inward_displacement_um)||![radial.live_founder_count,radial.live_descendant_count].every(v=>Number.isSafeInteger(v)&&v>=0)||radial.live_founder_count>group.ids.length||radial.live_founder_count+radial.live_descendant_count!==values.live_count||!Array.isArray(radial.shells)||radial.shells.length!==radii.length)throw Error('task.invalid_radial_metrics');
      for(const [index,shell] of radial.shells.entries())if(shell.radius_um!==radii[index]||!Number.isSafeInteger(shell.live_count)||shell.live_count<0||shell.live_count>values.live_count||!fraction(shell.live_fraction)||!fraction(shell.founder_ever_arrived_fraction)||!['volume_enrichment','founder_mean_residence_s','founder_mean_first_arrival_s'].every(k=>nonnegative(shell[k])))throw Error('task.invalid_radial_metrics');
    }
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
  return key({project,version_lock:record.submission?.version_lock ?? {kind:'synchronous',execution:record.replay?.execution??null},
    backend:record.submission?.execution.backend??'synchronous',task_contract:record.submission?.task_contract_version??null});
}
export function compareRuns(records) {
  if (records.length < 2) throw new Error('comparisonNeedTwo');
  if (records.some(r => r.status !== 'completed' || (r.localId && r.completeness !== 'complete') || !r.replay?.snapshots.at(-1)?.metrics?.by_group)) throw new Error('comparisonCompleteOnly');
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
        byGroup[group][metric] = {n:values.filter(Number.isFinite).length,mean,sd,interval:meanInterval(values)};
      }
    }
    const lock=runs[0].submission?.version_lock;
    const provenance=lock?`Task ${runs[0].submission.task_contract_version} · ${runs[0].submission.execution.backend} · registry ${lock.registry_sha256.slice(0,12)}`:'Synchronous result';
    return {label:runs[0].design_ref ? `${runs[0].design_ref.candidate_name} · ${runs[0].design_ref.candidate_id}` : runs[0].project.id,provenance,versionLock:lock??null,seeds,runs,byGroup};
  });
}

export function metricRows(replay) {
  return replay.snapshots.flatMap(({frame,metrics}) => Object.entries(metrics?.by_group ?? {}).map(([group,values]) =>
    [frame.frame_index,frame.time_s,metrics.observation_id,group,...METRIC_KEYS.map(key => values[key] ?? '')]));
}
export function radialMetricColumns(replay){
  const radii=[...new Set(replay.snapshots.flatMap(s=>Object.values(s.metrics?.by_group??{}).flatMap(v=>v.radial?.shells.map(shell=>shell.radius_um)??[])))].sort((a,b)=>a-b);
  if(!radii.length)return [];
  const columns=['mean_distance_um','mean_inward_displacement_um','live_founder_count','live_descendant_count'].map(key=>({label:`radial.${key}`,value:r=>r?.[key]??''}));
  for(const radius of radii)for(const key of ['live_count','live_fraction','volume_enrichment','founder_ever_arrived_fraction','founder_mean_residence_s','founder_mean_first_arrival_s'])columns.push({label:`radial.R${radius}um.${key}`,value:r=>r?.shells.find(s=>s.radius_um===radius)?.[key]??''});
  return columns;
}
export const csvCell = value => /[",\r\n]/.test(String(value)) ? '"'+String(value).replaceAll('"','""')+'"' : String(value);
