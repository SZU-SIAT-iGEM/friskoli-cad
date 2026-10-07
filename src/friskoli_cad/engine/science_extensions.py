"""Ordinary registered modules for modular-spatial-v1.

Effects are proposals only. The owning runtime settles and commits them.
"""
import math
import numpy as np

from friskoli_cad.science import processes as law
from .module_api import Effect, ModuleProposal, thaw
from .declarations import port, number, SPECIES

PROFILE = 'modular-spatial-v1'


def array_parameter(unit='1', items=None):
    return {'type': 'array', 'unit': unit, 'items': items or {'type': 'number'}}


RECORD = {'type': 'object', 'additionalProperties': True}
SCALAR = port('global.scalar', 'scalar', '1')
CELL = port('cell.scalar', 'scalar', '1')
AMOUNT = port('global.scalar', 'amount', 'molecule')
RECORD_PORT = port('global.record', 'record', '1')


class ScientificModule:
    world_access = 'transactional'

    def __init__(self, identifier, label, stage, scope, inputs, outputs, parameters, state,
                 equation, description, function, *, reads=(), effects=(), writes=(), sources=()):
        self.function = function
        self.manifest = {'protocol_version': '0.2.0', 'id': identifier, 'version': '1.0.0',
            'scope': scope, 'phase': {'prepare': 3, 'field': 4, 'physiology': 8, 'lifecycle': 9, 'observation': 20}[stage],
            'scientific_role': 'mechanism', 'maturity': 'exploratory', 'description': description,
            'inputs': inputs, 'outputs': outputs, 'parameters': parameters,
            'state': {key: {**{k: v for k, v in spec.items() if k not in ('quantity', 'species_parameter')}, 'on_division': 'copy' if spec['shape'].startswith('cell.') else 'not_applicable', 'on_death': 'discard', 'on_migration': 'conservative_regrid' if spec['shape'] == 'field.scalar' else 'copy'} for key, spec in state.items()},
            'initial_outputs': list(outputs)}
        self.execution_contract = {'api_version': '0.1.0', 'stage': stage, 'reads': list(reads),
            'writes': list(writes), 'effects': list(effects), 'backends': ['numpy-cpu'],
            'effect_targets': {kind: ['$species' if kind.startswith('field.') else '$owner'] for kind in effects}}
        # These effect contracts return proposals, whose final values become
        # visible only after the system has settled all providers in the stage.
        settled_by_effect = {
            'field.uptake': ('accepted_amount', 'accepted_flux', 'cumulative_uptake'),
            'field.delta': ('net_input', 'cumulative_net_input', 'consumed', 'cumulative_consumed'),
            'geometry.growth': ('intracellular_molecules', 'volume', 'used_molecules', 'growth_rate', 'blocked'),
            'geometry.elongation': ('length',),
            'motion.paths': ('position', 'heading', 'blocked'),
        }
        self.execution_contract['settled_outputs'] = sorted({
            name for kind in effects for name in settled_by_effect.get(kind, ()) if name in outputs})
        self.declaration = {'label': label, 'category': 'mechanism', 'mathematics': {
            'kind': 'equations', 'equations': [{'latex': equation, 'symbols': {}}],
            'algorithm': description, 'implementation': 'friskoli_cad.engine.science_extensions.ScientificModule',
            'verification': {'status': 'unreviewed', 'tests': []},
            'assumptions': ['modular-spatial-v1; explicit registered effects, transactional commit.',
                'Parameters must be supplied with provenance; no experimental strain calibration.',
                'Scope and source support: docs/science/modular-processes.md.', *sources]}}

    def project_preflight(self, parameters, project, owner_id):
        p = thaw(parameters)
        if self.manifest['id'] == 'signal.pts_methylation':
            from dataclasses import fields
            from friskoli_cad.science.pts_methylation import PTSMethylationParameters, advance
            par = PTSMethylationParameters(**{f.name: p[f.name.lower()] for f in fields(PTSMethylationParameters)})
            advance(0., p['initial_ei_fraction'], p['initial_methylation'], p['initial_chey_p_um'], 0., par)
        if 'minimum_tumble_rate_s' in p and p['minimum_tumble_rate_s'] > p['maximum_tumble_rate_s']:
            raise ValueError('minimum tumble rate must not exceed maximum tumble rate')
        shape = tuple(reversed(project['domain']['counts_xyz']))
        for name in ('lower_um', 'upper_um', 'velocity_xyz_um_s', 'release_position_um'):
            if name in p and (np.asarray(p[name]).shape != (3,) or not np.isfinite(p[name]).all()):
                raise ValueError(name + ' requires three finite XYZ values')
        if 'lower_um' in p and np.any(np.asarray(p['lower_um']) >= np.asarray(p['upper_um'])):
            raise ValueError('lower_um must be strictly below upper_um')
        if 'values_um' in p:
            def dimensions(value):
                if not isinstance(value, (list, tuple)): return ()
                if not value: return (0,)
                child = dimensions(value[0])
                if any(dimensions(v) != child for v in value): raise ValueError('values_um must be rectangular')
                return (len(value),) + child
            if dimensions(p['values_um']) != shape: raise ValueError('values_um must match the full ZYX grid shape')
            def nonnegative_values(value):
                if isinstance(value, (list, tuple)):
                    for item in value: nonnegative_values(item)
                elif not math.isfinite(value) or value < 0: raise ValueError('values_um requires finite nonnegative concentrations')
            nonnegative_values(p['values_um'])
        if 'events' in p: law.schedule_amount(p['events'], 0., 0., initialize=True)
        if 'properties' in p: law.validate_property_catalog(p['properties'])
        if 'velocity_source' in p and not p['velocity_source'].strip(): raise ValueError('velocity_source requires provenance')
        if 'distribution' in p:
            if p['distribution'] not in ('normal', 'uniform') or p['minimum'] > p['maximum']: raise ValueError('Invalid distribution or minimum/maximum')
        if 'vertices_xyz' in p: law.mesh_geometry(p['vertices_xyz'], p['faces'], scale_um=p['scale_um'])
        if 'route' in p and p['route'] not in ('glucose', 'cellobiose'): raise ValueError('Unsupported hydrolysis route')
        if 'daughter_fraction' in p and not 0 < p['daughter_fraction'] < 1: raise ValueError('daughter_fraction must be strictly between zero and one')

    def initialize(self, context):
        return self.function(context, True)

    def propose(self, context):
        return self.function(context, False)


def _proposal(outputs, state=None, effects=()):
    return ModuleProposal(outputs, state or {}, tuple(effects))


def _mask(context):
    shape = tuple(context.world['grid_shape_zyx'])
    p = context.parameters
    z, y, x = np.indices(shape)
    spacing = np.asarray(context.world['spacing_xyz'])
    coordinates = np.stack(((x + .5) * spacing[0], (y + .5) * spacing[1], (z + .5) * spacing[2]), axis=-1)
    lower, upper = np.asarray(p['lower_um']), np.asarray(p['upper_um'])
    if lower.shape != (3,) or upper.shape != (3,) or np.any(lower >= upper):
        raise ValueError('spatial support requires increasing XYZ lower/upper bounds')
    return np.all((coordinates >= lower) & (coordinates < upper), axis=-1) & ~np.asarray(context.world['blocked'], bool).reshape(shape)


GRID_READS = ('grid_shape_zyx', 'spacing_xyz', 'blocked', 'molecules_per_uM_voxel')
BOX = {'lower_um': array_parameter('um'), 'upper_um': array_parameter('um')}


def scheduled_source(c, initial):
    amount = law.schedule_amount(c.parameters['events'], c.time_s, c.dt_s, initialize=initial)
    mask = _mask(c)
    if amount and not mask.any():
        raise ValueError('scheduled source has no fluid support')
    delta = mask.astype(float) * amount / max(1, int(mask.sum()))
    cumulative = (0. if initial else c.state['cumulative_input']) + amount
    return _proposal({'released_amount': amount, 'cumulative_input': cumulative}, {'cumulative_input': cumulative},
                     [Effect('field.delta', c.parameters['species'], delta)])


def initial_array(c, initial):
    if not initial: return _proposal({'initialized': 1.})
    values = law.nonnegative(c.parameters['values_um'])
    if values.shape != tuple(c.world['grid_shape_zyx']):
        raise ValueError('initial array must use exact ZYX grid shape')
    # Obstacle voxels are solid, so whatever the array carries there has no
    # physical meaning. Zero it the same way field.diffusive_local does instead
    # of rejecting the project: the strict form made every edit of an obstacle's
    # geometry a hard failure, and the array holds the whole ZYX grid, so the
    # author had no way to repair it by hand.
    values = values.copy()
    values[np.asarray(c.world['blocked'], bool).reshape(values.shape)] = 0.
    return _proposal({'initialized': 1.},
                     effects=[Effect('field.initial', c.parameters['species'], values)])


def reservoir_exchange(c, initial):
    # A restored checkpoint hands state scalars back as read-only arrays, so the
    # accumulator is coerced to a Python float before any in-place update.
    cumulative = 0. if initial else float(np.asarray(c.state['cumulative_net_input']).ravel()[0])
    delta = np.zeros(tuple(c.world['grid_shape_zyx']))
    if not initial:
        concentration = np.asarray(c.world['fields'][c.parameters['species']]).reshape(delta.shape)
        _, change = law.exchange(concentration, c.parameters['target_um'], c.parameters['rate_s'], c.dt_s, _mask(c))
        delta = change * c.world['molecules_per_uM_voxel']
    amount = float(delta.sum()); cumulative = cumulative + amount
    return _proposal({'net_input': amount, 'cumulative_net_input': cumulative}, {'cumulative_net_input': cumulative},
                     [Effect('field.delta', c.parameters['species'], delta)])


def oxygen_consumption(c, initial):
    cumulative = 0. if initial else c.state['cumulative_consumed']
    concentration = np.asarray(c.world['fields'][c.parameters['species']]).reshape(c.world['grid_shape_zyx'])
    rate, half = c.parameters['maximum_rate_um_s'], c.parameters['half_saturation_um']
    if half <= 0:
        raise ValueError('oxygen half-saturation must be positive')
    desired = np.zeros_like(concentration) if initial else rate * concentration / (half + concentration) * c.dt_s
    consumed = np.minimum(concentration, desired) * c.world['molecules_per_uM_voxel']
    consumed[np.asarray(c.world['blocked'], bool).reshape(consumed.shape)] = 0.
    amount = float(consumed.sum())
    return _proposal({'consumed': amount, 'cumulative_consumed': cumulative + amount},
        {'cumulative_consumed': cumulative + amount}, [Effect('field.delta', c.parameters['species'], -consumed)])


def advection(c, initial):
    p = c.parameters
    before = np.asarray(c.world['fields'][p['species']]).reshape(c.world['grid_shape_zyx'])
    velocity = p['velocity_xyz_um_s']
    if not p['velocity_source'].strip():
        raise ValueError('prescribed velocity requires a source or constructed-assumption reference')
    after = before if initial else law.upwind_advection(before, velocity, c.world['spacing_xyz'], c.dt_s,
                periodic=p['periodic'], blocked=c.world['blocked'])
    delta = (after - before) * c.world['molecules_per_uM_voxel']
    return _proposal({'net_input': 0.}, effects=[Effect('field.transport', p['species'], delta)])


def medium_properties(c, initial):
    records = thaw(c.parameters['properties'])
    law.validate_property_catalog(records)
    return _proposal({'properties': records})


def viscosity(c, initial):
    p = c.parameters
    value = float(law.stokes_einstein_scale(p['reference_diffusivity_um2_s'], p['temperature_k'],
                  p['reference_temperature_k'], p['viscosity_pa_s'], p['reference_viscosity_pa_s']))
    return _proposal({'diffusivity': value, 'viscosity': p['viscosity_pa_s']},
                     effects=[Effect('field.diffusivity', p['species'], value)])


def scalar_control(kind):
    def evaluate(c, initial):
        p, count = c.parameters, len(c.entity_ids)
        value = np.asarray(c.inputs.get('value', np.full(count, p.get('initial_value', 0.))), float)
        if kind == 'broadcast':
            return _proposal({'value': np.full(count, float(value))})
        if kind == 'scale':
            return _proposal({'value': value * p['factor']})
        if kind == 'delay':
            previous = np.full(count, p['initial_value'], dtype=float) if initial else np.asarray(c.state['previous'])
            return _proposal({'value': previous}, {'previous': value})
        if kind == 'lowpass':
            result = np.full(count, p['initial_value'], dtype=float) if initial else law.lowpass(c.state['value'], value, c.dt_s, p['tau_s'])
            return _proposal({'value': result}, {'value': result})
        elapsed = np.zeros(count) if initial else np.where(value >= p['threshold'], np.asarray(c.state['elapsed']) + c.dt_s, 0.)
        return _proposal({'value': (elapsed >= p['duration_s']).astype(float)}, {'elapsed': elapsed})
    return evaluate


def geometry_readout(c, initial):
    length, diameter = np.asarray(c.world['length_um']), np.asarray(c.world['diameter_um'])
    volume = math.pi * diameter ** 2 * (length - diameter) / 4 + math.pi * diameter ** 3 / 6
    return _proposal({'volume': volume, 'surface_area': math.pi * diameter * length,
                      'aspect_ratio': length / diameter, 'cylinder_length': length - diameter})


def extension_modules():
    result = []
    def add(*args, **kwargs):
        result.append(ScientificModule(*args, **kwargs))
    for kind in ('broadcast', 'scale', 'delay', 'lowpass', 'sustained_threshold'):
        parameters = {'factor': number(minimum=-1e300)} if kind == 'scale' else {}
        if kind in ('delay', 'lowpass'):
            parameters['initial_value'] = number(minimum=-1e300)
        if kind == 'lowpass': parameters['tau_s'] = number('s', minimum=1e-300)
        if kind == 'sustained_threshold': parameters.update(threshold=number(minimum=-1e300), duration_s=number('s', minimum=1e-300))
        states = {'previous': CELL} if kind == 'delay' else {'value': CELL} if kind == 'lowpass' else {'elapsed': port('cell.scalar', 'elapsed_time', 's')} if kind == 'sustained_threshold' else {}
        add('control.' + kind, '基础控制 / ' + kind, 'prepare', 'population',
            {'value': SCALAR if kind == 'broadcast' else CELL}, {'value': CELL}, parameters, states,
            r'y=f(x,s,\Delta t)', 'Explicit discrete delay, exact first-order filter or continuous sampled-threshold duration; initial state is required.', scalar_control(kind))
    add('geometry.capsule_derived', '胶囊派生量 / Capsule geometry', 'prepare', 'population', {},
        {'volume': port('cell.scalar', 'cell_volume', 'um^3'), 'surface_area': port('cell.scalar', 'surface_area', 'um^2'),
         'aspect_ratio': CELL, 'cylinder_length': port('cell.scalar', 'length', 'um')}, {}, {},
        r'V=\pi d^2(L-d)/4+\pi d^3/6,\ A=\pi d L', 'Known capsule geometry; length includes both hemispheres.',
        geometry_readout, reads=('length_um', 'diameter_um'))
    add('source.spatial_schedule', '空间输入日程 / Spatial schedule', 'field', 'environment', {},
        {'released_amount': AMOUNT, 'cumulative_input': AMOUNT},
        {'species': SPECIES, **BOX, 'events': array_parameter(items=RECORD)}, {'cumulative_input': AMOUNT},
        r'Q=\int_t^{t+\Delta t}r(s)ds+\sum_{t<t_k\le t+\Delta t}q_k',
        'External finite pulses and rates on explicit physical box support; t=0 applied only at initialization.',
        scheduled_source, reads=GRID_READS, effects=('field.delta',))
    add('field.initial_array', '任意初始场 / Initial array', 'field', 'environment', {}, {'initialized': SCALAR},
        {'species': SPECIES, 'values_um': array_parameter('uM', {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'number', 'minimum': 0}}})}, {},
        r'C(x,0)=C_0(x)', 'Explicit ZYX array in uM; initial stock, not recurring supply.',
        initial_array, reads=GRID_READS, effects=('field.initial',), writes=('field.initial',))
    for identifier, label in [('field.boundary_exchange', '有限浓度交换 / Finite concentration exchange'),
                              ('reaction.oxygen_exchange', '气液氧交换 / Oxygen exchange')]:
        add(identifier, label, 'field', 'environment', {}, {'net_input': AMOUNT, 'cumulative_net_input': AMOUNT},
            {'species': SPECIES, **BOX, 'target_um': number('uM'), 'rate_s': number('1/s')}, {'cumulative_net_input': AMOUNT},
            r'C^{n+1}=C_*+(C^n-C_*)e^{-k\Delta t}',
            'First-order exchange on the declared box; positive and negative external transfers are recorded.',
            reservoir_exchange, reads=(*GRID_READS, 'fields'), effects=('field.delta',))
    add('reaction.oxygen_consumption', '氧消耗 / Oxygen consumption', 'field', 'environment', {},
        {'consumed': AMOUNT, 'cumulative_consumed': AMOUNT}, {'species': SPECIES, 'maximum_rate_um_s': number('uM/s'),
        'half_saturation_um': number('uM', minimum=1e-300)}, {'cumulative_consumed': AMOUNT},
        r'Q=\min(C,\Delta t\,r_{max}C/(K+C))V N_A',
        'Prescribed distributed respiratory sink; requires an explicit oxygen field. No automatic growth coupling.',
        oxygen_consumption, reads=(*GRID_READS, 'fields'), effects=('field.delta',))
    add('field.advection_upwind', '守恒平流 / Conservative advection', 'field', 'environment', {}, {'net_input': AMOUNT},
        {'species': SPECIES, 'velocity_xyz_um_s': array_parameter('um/s'), 'velocity_source': {'type': 'string'},
         'periodic': {'type': 'boolean'}}, {}, r'\partial_t C+\nabla\cdot(uC)=0',
        'Uniform prescribed velocity, donor-cell finite volumes, CFL substeps, no-flux or periodic boundaries; not CFD.',
        advection, reads=(*GRID_READS, 'fields'), effects=('field.transport',), writes=('field.transport',))
    add('medium.property_catalog', '条件物性目录 / Conditional properties', 'prepare', 'environment', {}, {'properties': RECORD_PORT},
        {'properties': array_parameter(items=RECORD)}, {}, r'p=p(T,\mathrm{conditions})',
        'Data-only property records; null remains unknown and does not produce computation.', medium_properties)
    add('medium.viscosity_diffusion', '黏度扩散响应 / Viscosity diffusion', 'field', 'environment', {},
        {'diffusivity': port('global.scalar', 'diffusivity', 'um^2/s'), 'viscosity': port('global.scalar', 'dynamic_viscosity', 'Pa*s')},
        {'species': SPECIES, 'reference_diffusivity_um2_s': number('um^2/s'), 'temperature_k': number('K', minimum=1e-300),
         'reference_temperature_k': number('K', minimum=1e-300), 'viscosity_pa_s': number('Pa*s', minimum=1e-300),
         'reference_viscosity_pa_s': number('Pa*s', minimum=1e-300)}, {}, r'D=D_r(T/T_r)(\eta_r/\eta)',
        'Stokes-Einstein scaling at fixed hydrodynamic size in Newtonian dilute continuum; not a bacterial speed law.',
        viscosity, effects=('field.diffusivity',), writes=('medium.viscosity',),
        sources=('https://goldbook.iupac.org/terms/view/12260',))
    for module in result:
        if module.manifest['id'] == 'geometry.capsule_derived': module.refresh_after_lifecycle = True
        if module.manifest['id'] == 'reaction.oxygen_consumption': module.effect_accounting = {'field.delta': 'reaction_net'}
    return result


def modular_registry():
    from .science_adapters import adapted_modules
    from .science_advanced import advanced_modules
    from .standard_modules import standard_modules
    from .module_registry import ModuleRegistry
    return ModuleRegistry([*adapted_modules(), *extension_modules(), *advanced_modules(), *standard_modules()], PROFILE)


def make_modular_example(example='modular-foundation'):
    from .presets import make_example
    return make_example(example)


def modular_template_catalog():
    from .presets import template_catalog
    return template_catalog()
