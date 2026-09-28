import assert from 'node:assert/strict';
import test from 'node:test';
import { cellHistory, normalizeReplay, xyAngleDegrees } from '../src/friskoli_cad/web/replay.mjs';

function cell(id, length = 2) {
  return { id, group_id: 'group_1', position_um: [1, 1, .5], orientation_xyzw: [0, 0, 0, 1],
    geometry: { shape: 'capsule', length_um: length, diameter_um: .8 }, channels: {} };
}

function payload() {
  return { replay_format_version: '0.1.0', project_id: 'example',
    run: { protocol_version: '0.1.0', run_id: 'run_1', channels: {} },
    domain: { counts_xyz: [2, 2, 1], spacing_um_xyz: [2, 2, 1] },
    snapshots: [
      { frame: { protocol_version: '0.1.0', frame_version: '0.2.0', run_id: 'run_1', frame_index: 0,
        time_s: 0, cells: [cell('parent')], events: [] }, concentrations: {} },
      { frame: { protocol_version: '0.1.0', frame_version: '0.2.0', run_id: 'run_1', frame_index: 1,
        time_s: 1, cells: [cell('parent', 1.5), cell('child', 1.5)],
        events: [{ type: 'division', time_s: 1, parent_id: 'parent', child_id: 'child' }] }, concentrations: {} },
    ] };
}

test('division adds a stable child ID and history follows one cell', () => {
  const replay = normalizeReplay(payload());
  assert.deepEqual(cellHistory(replay, 'parent').map(item => item.frame_index), [0, 1]);
  assert.deepEqual(cellHistory(replay, 'child').map(item => item.frame_index), [1]);
  assert.equal(xyAngleDegrees([0, 0, 0, 1]), 0);
});

test('viewer rejects frames that disagree with the event history', () => {
  const replay = payload();
  replay.snapshots[1].frame.events = [];
  assert.throws(() => normalizeReplay(replay), Error);
});

test('viewer rejects unsupported frame versions and malformed fields', () => {
  const replay = payload();
  replay.snapshots[1].frame.frame_version = '0.3.0';
  assert.throws(() => normalizeReplay(replay), Error);
  const replayWithField = payload();
  replayWithField.snapshots[0].concentrations.oxygen = { unit: 'uM', values_zyx: [[[1]]] };
  assert.throws(() => normalizeReplay(replayWithField), Error);
});
