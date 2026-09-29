// Object kinds the Space view can place. A kind is usable only when its backend module is in the
// catalog; kinds with module: null are placeholders whose protocol declaration does not exist yet.
// The protocol currently declares only cell populations.
export const PLACEABLES = [
  { kind: 'population', label: 'populationVolume', hint: 'populationVolumeHint', icon: 'cell',
    tool: 'population', module: 'population.static@1.0.0' },
  { kind: 'obstacle', label: 'obstacleKind', hint: 'obstacleKindHint', icon: 'wall', tool: null, module: null },
  { kind: 'source', label: 'sourceKind', hint: 'sourceKindHint', icon: 'source', tool: null, module: null },
  { kind: 'fiber', label: 'fiberKind', hint: 'fiberKindHint', icon: 'fiber', tool: null, module: null },
];

// 'ready' can be placed now, 'missing' is declared but absent from this backend, 'undeclared' awaits the protocol.
export function availablePlaceables(modules, capabilities = null) {
  return PLACEABLES.map(item => ({ ...item,
    status: !item.module ? 'undeclared' : modules.has(item.module) &&
      (!capabilities || capabilities.placeables.some(p => p.kind === item.kind && p.module === item.module)) ? 'ready' : 'missing' }));
}
