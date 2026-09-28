export function normalizeReplay(payload) {
  if (payload?.replay_format_version !== '0.1.0') throw new Error('badEnvelope');
  if (payload?.run?.protocol_version !== '0.1.0') throw new Error('badGraphVersion');
  if (!Array.isArray(payload.snapshots) || payload.snapshots.length === 0) throw new Error('emptyFrames');
  const [nx, ny, nz] = payload.domain?.counts_xyz ?? [];
  const spacing = payload.domain?.spacing_um_xyz;
  if (![nx, ny, nz].every(n => Number.isInteger(n) && n > 0) ||
      !Array.isArray(spacing) || spacing.length !== 3 || !spacing.every(n => Number.isFinite(n) && n > 0)) {
    throw new Error('badDomain');
  }
  const runId = payload.run.run_id;
  let version;
  let previousTime = -1;
  let alive = new Map();
  const seen = new Set();
  payload.snapshots.forEach(({ frame, concentrations }, index) => {
    const currentVersion = frame?.frame_version ?? '0.1.0';
    if (!['0.1.0', '0.2.0'].includes(currentVersion) || (version && version !== currentVersion)) {
      throw new Error('badFrameVersion');
    }
    version = currentVersion;
    if (frame.protocol_version !== '0.1.0' || frame.run_id !== runId || frame.frame_index !== index ||
        !Number.isFinite(frame.time_s) || frame.time_s <= previousTime ||
        (index === 0 && (frame.time_s !== 0 || frame.events?.length !== 0)) ||
        !Array.isArray(frame.events) || !Array.isArray(frame.cells)) {
      throw new Error(`badFrame:${index}`);
    }
    const nextAlive = new Map(alive);
    for (const event of frame.events) {
      if (event.type === 'division' && nextAlive.has(event.parent_id) && !seen.has(event.child_id)) {
        nextAlive.set(event.child_id, nextAlive.get(event.parent_id));
        seen.add(event.child_id);
      } else if (event.type === 'birth' && !seen.has(event.cell_id)) {
        nextAlive.set(event.cell_id, event.group_id);
        seen.add(event.cell_id);
      } else if (event.type === 'death' && nextAlive.has(event.cell_id)) {
        nextAlive.delete(event.cell_id);
      } else {
        throw new Error(`badEvent:${index}`);
      }
    }
    const observed = new Map();
    for (const cell of frame.cells) {
      if (observed.has(cell.id) || !Array.isArray(cell.position_um) || cell.position_um.length !== 3 ||
          !cell.position_um.every(Number.isFinite) || !Array.isArray(cell.orientation_xyzw) ||
          cell.orientation_xyzw.length !== 4 || !cell.orientation_xyzw.every(Number.isFinite)) {
        throw new Error(`badCell:${index}`);
      }
      observed.set(cell.id, cell.group_id);
      if (currentVersion === '0.2.0' && !Object.hasOwn(cell, 'geometry')) throw new Error('geometryMissing');
    }
    if (index === 0) {
      alive = observed;
      for (const id of observed.keys()) seen.add(id);
    } else {
      if (observed.size !== nextAlive.size || [...observed].some(([id, group]) => nextAlive.get(id) !== group)) {
        throw new Error(`frameEventMismatch:${index}`);
      }
      alive = nextAlive;
    }
    if (!concentrations || typeof concentrations !== 'object') throw new Error('fieldsMissing');
    for (const field of Object.values(concentrations)) {
      if (!Array.isArray(field.values_zyx) || field.values_zyx.length !== nz ||
          field.values_zyx.some(layer => !Array.isArray(layer) || layer.length !== ny ||
            layer.some(row => !Array.isArray(row) || row.length !== nx || !row.every(Number.isFinite)))) {
        throw new Error('badFieldShape');
      }
    }
    previousTime = frame.time_s;
  });
  return payload;
}

export function cellHistory(replay, id) {
  return replay.snapshots.flatMap(({ frame }) => {
    const cell = frame.cells.find(item => item.id === id);
    return cell ? [{ frame_index: frame.frame_index, time_s: frame.time_s, cell }] : [];
  });
}

export function frameCellsById(frame) {
  return new Map(frame.cells.map(cell => [cell.id, cell]));
}

export function xyAngleDegrees(orientation) {
  const [x, y, z, w] = orientation;
  return Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)) * 180 / Math.PI;
}
