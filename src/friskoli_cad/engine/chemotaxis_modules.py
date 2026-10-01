"""Registered mechanisms for the explicitly versioned N3 scientific profile.

Defaults are constructed, editable numerical examples. Literature supports the
named mechanisms; these values do not assert calibration of the Friskoli strain.
"""
from copy import deepcopy
from importlib.resources import files
import json

from .pts_modules import PTSModule, port, number, SPECIES, SIGNAL_PARAMETERS, AREA, COPIES
from .spatial_modules import spatial_registry, FIELD, POSITION
from .runtime import ModuleRegistry
from .degradation import propose_contact_degradation

PROFILE = 'chemotaxis-spatial-v1'
BIAS = port('cell.scalar', 'motor_bias', '1')
CHEY = port('cell.scalar', 'chey_phosphorylated_concentration', 'uM')
GROWTH = port('cell.scalar', 'specific_growth_rate', '1/min')
VOLUME = port('cell.scalar', 'cell_volume', 'um^3')
ENZYME = port('cell.scalar', 'enzyme_copies', 'molecule')


def signed(unit='1'):
    return {'type': 'number', 'unit': unit}


def direct_bulk_hydrolysis_adapter(material, capsules, enzyme_copies, parameters, dt_s):
    from friskoli_cad.science.materials import direct_hydrolysis_rate
    effective_rate = float(direct_hydrolysis_rate(1., material.remaining_molecules,
        parameters['initial_molecules'], turnover_s=parameters['kcat_s']))
    return propose_contact_degradation(material, capsules, enzyme_copies,
        kcat_s=effective_rate, contact_range_um=parameters['contact_range_um'], dt_s=dt_s)


class ChemotaxisModule(PTSModule):
    def __init__(self, *args, defaults=None, evidence='N3-constructed'):
        super().__init__(*args)
        self.default_parameters = deepcopy(defaults or {})
        self.declaration['mathematics'].update(
            implementation='friskoli_cad.engine.chemotaxis_modules.ChemotaxisModule',
            verification={'status': 'tested', 'tests': ['tests/test_n3_science.py', 'tests/test_chemotaxis_runtime.py']},
            assumptions=[f'{PROFILE}; explicit operator splitting, no hydrodynamic or contact-force prediction.',
                'Constructed/source-derived exploratory parameters; no experimental calibration.',
                f'Evidence: {evidence}; docs/science/n3-mechanisms.md. Per-cell state has explicit lifecycle inheritance.'])


def chemotaxis_registry():
    modules = []
    for old in spatial_registry()._modules.values():
        if old.manifest['id'] == 'motion.unbiased_run_tumble':
            continue
        module = deepcopy(old)
        module.declaration['mathematics']['assumptions'] = [
            f'{PROFILE}; source equations retain their declared units and mechanisms.',
            'Geometry and inventories evolve only through registered owners; parameters remain exploratory.',
            'See docs/science/n3-mechanisms.md for splitting, limits and source differences.']
        if module.manifest['id'] == 'field.diffusive_local':
            module.manifest['version'] = '2.0.0'
            module.manifest['parameters'].update({f'gradient_{a}_um_per_um': signed('uM/um') for a in 'xyz'})
            module.default_parameters = {'species': 'nutrient', 'diffusivity_um2_s': 1.,
                **{f'gradient_{a}_um_per_um': 0. for a in 'xyz'}}
            module.declaration['mathematics']['algorithm'] += (
                ' Initialization is an explicit linear field about the domain center using the project species mean;'
                ' negative initial fluid voxel concentrations are rejected. Gradients only initialize; diffusion then evolves freely.')
        if hasattr(module, 'object_types'):
            for obj in module.object_types:
                obj['initializer']['data_modules'] = [key.replace('field.diffusive_local@1.0.0', 'field.diffusive_local@2.0.0')
                    for key in obj['initializer']['data_modules']]
        if module.manifest['id'] == 'pts.capsule_area':
            obj = json.loads(files('friskoli_cad').joinpath('engine', 'declarations', 'population.object.json').read_text(encoding='utf-8'))
            obj['initializer'] = {'adapter': 'population.block@1', 'module': 'pts.capsule_area@1.0.0', 'data_modules': []}
            next(p for p in obj['properties'] if p['path'] == 'count')['maximum'] = 256
            module.object_types = [obj]
        modules.append(module)
    modules.extend([
        ChemotaxisModule('signal.constant_bias', '无趋化对照 / Constant motor bias', 5, 'population', {},
            {'motor_bias': BIAS}, {'bias': number(maximum=1)}, {}, r'b_i=b_0', {'b_0': 'parameters.bias'},
            'Explicit constant-bias control. Nutrient uptake can remain active without feeding back to movement.', defaults={'bias': .25}),
        ChemotaxisModule('signal.concentration_memory', '浓度记忆 A / Concentration memory A', 6, 'population',
            {'concentration': port('cell.scalar', 'concentration', 'uM', True), 'motor_bias': BIAS},
            {'memory': port('cell.scalar', 'concentration_memory', 'uM', True), 'motor_bias': BIAS},
            {'species': SPECIES, 'memory_tau_s': number('s'), 'gradient_strength_per_um': number('1/uM'), 'initial_memory_um': number('uM')},
            {'memory': ('cell.scalar', 'uM')}, r'\dot m=(C-m)/\tau,\quad b=\min(1,b_{PTS}\exp[-g(C-m)])',
            {'C': 'inputs.concentration [uM]', 'm': 'state.memory [uM]', 'g': 'parameters.gradient_strength_per_um [1/uM]'},
            'Source A concentration memory and exponential motor modulation; exponent is explicitly bounded to ±20 as in the source.',
            defaults={'species': 'nutrient', 'memory_tau_s': 3., 'gradient_strength_per_um': 3., 'initial_memory_um': 1.}, evidence='source A motion'),
        ChemotaxisModule('signal.chey_memory', 'CheY-P 记忆 B / CheY-P memory B', 6, 'population',
            {'chey_p': CHEY}, {'memory': port('cell.scalar', 'chey_memory', 'uM'), 'effective_chey': CHEY, 'motor_bias': BIAS},
            {'adaptation_tau_s': number('s'), 'baseline_um': number('uM'), 'total_um': number('uM'),
             'motor_hill': number(), 'motor_half_um': number('uM'), 'initial_memory_um': number('uM')},
            {'memory': ('cell.scalar', 'uM')}, r'\dot m=(Y-m)/\tau,\quad Y_{eff}=\mathrm{clip}(Y_0+Y-m,0,Y_T)',
            {'Y': 'inputs.chey_p [uM]', 'm': 'state.memory [uM]', 'Y_0': 'parameters.baseline_um [uM]'},
            'Source B low-pass CheY-P adaptation; the Hill motor readout uses the adapted signal. This is not an MCP methylation model.',
            defaults={'adaptation_tau_s': 3., 'baseline_um': 2., 'total_um': 10., 'motor_hill': 4., 'motor_half_um': 3., 'initial_memory_um': 2.}, evidence='source B adaptation'),
        ChemotaxisModule('signal.mcp_adaptation', 'MCP 受体适应 / Reduced MCP adaptation', 5, 'population',
            {'concentration': port('cell.scalar', 'concentration', 'uM', True)},
            {'activity': port('cell.scalar', 'receptor_activity', '1'), 'adaptation': port('cell.scalar', 'receptor_methylation', '1'),
             'chey_p': CHEY, 'motor_bias': BIAS},
            {'species': SPECIES, 'cluster_size': number(), 'inactive_binding_um': number('uM'), 'active_binding_um': number('uM'),
             'methylation_energy': number(), 'methylation_reference': signed(), 'adaptation_rate_s': number('1/s'),
             'baseline_activity': number(maximum=1), **SIGNAL_PARAMETERS, 'initial_chey_p_um': number('uM')},
            {'adaptation': ('cell.scalar', '1'), 'chey_p': ('cell.scalar', 'uM')},
            r'a=[1+e^{N[\alpha(m_0-m)+\log((1+L/K_i)/(1+L/K_a))]}]^{-1},\quad \dot m=k(a_0-a)',
            {'a': 'outputs.activity', 'L': 'inputs.concentration [uM]', 'm': 'outputs.adaptation'},
            'Reduced MWC receptor with declared linear adaptation feedback. Initialize methylation adapted to local ligand. CheA activity feeds a frozen-activity CheY step. Nutrient background is separately registered.',
            defaults={'species': 'ligand', 'cluster_size': 6., 'inactive_binding_um': 1., 'active_binding_um': 100.,
                'methylation_energy': 1., 'methylation_reference': 0., 'adaptation_rate_s': 1., 'baseline_activity': .5,
                'ei_total_um': 1., 'ei_dephos_per_molecule': .01, 'ei_rephos_s': 1., 'ei_chea_inhibition_um': 1.,
                'chea_total_um': 1., 'chey_total_um': 10., 'chey_phos_per_um_s': 1., 'chey_dephos_s': 1.,
                'motor_hill': 4., 'motor_half_um': 3.5, 'initial_chey_p_um': 10./3.}, evidence='Tu, Shimizu & Berg 2008; constructed linear F(a)'),
        ChemotaxisModule('motion.hazard_run_tumble', '信号驱动游走 / Signal-driven run and tumble', 7, 'population',
            {'motor_bias': BIAS}, {'position': POSITION, 'heading': port('cell.vector', 'heading', '1'),
             'blocked': port('cell.scalar', 'motion_blocked', '1'), 'turns': port('cell.scalar', 'turn_count', '1'),
             'tumble_phase': port('cell.scalar', 'tumble_phase', '1'), 'hazard_remaining': port('cell.scalar', 'remaining_hazard', '1')},
            {'tumble_mode': {'type': 'string'}, 'turn_kernel': {'type': 'string'}, 'speed_um_s': number('um/s'),
             'minimum_tumble_rate_s': number('1/s'), 'maximum_tumble_rate_s': number('1/s'), 'tumble_duration_s': number('s')},
            {'heading': ('cell.vector', '1'), 'remaining_hazard': ('cell.scalar', '1'), 'dwell_remaining': ('cell.scalar', 's')},
            r'\lambda=\lambda_{min}+(\lambda_{max}-\lambda_{min})b,\quad H\sim Exp(1),\quad \dot H=-\lambda',
            {'b': 'inputs.motor_bias at the committed boundary', 'H': 'state.remaining_hazard', '\\lambda': 'run-to-tumble hazard [1/s]'},
            'The committed motor state drives the next numerical interval. Instantaneous turns and finite fixed dwell are separate named modes. Collision is conservative geometric blocking; event clocks continue while blocked.',
            defaults={'tumble_mode': 'instant', 'turn_kernel': 'isotropic', 'speed_um_s': 5.,
                'minimum_tumble_rate_s': .1, 'maximum_tumble_rate_s': 3., 'tumble_duration_s': .1}),
        ChemotaxisModule('field.ideal_local_reservoir', '恒定营养背景 / Constant nutrient reservoir', 1, 'environment', {},
            {'concentration': FIELD, 'cumulative_supply': port('global.scalar', 'supplied_amount', 'molecule', True)},
            {'species': SPECIES}, {'cumulative_supply': ('global.scalar', 'molecule')},
            r'C(x,t)=C_0,\quad Q_{external}=\sum_i U_i', {'C_0': 'project species concentration [uM]', 'Q_{external}': 'outputs.cumulative_supply [molecule]'},
            'Uniform, externally maintained nutrient background; accepted uptake is entered in a replenishment ledger. Separate ligand fields remain finite. This is a case assumption, not a general MCP definition.', defaults={'species': 'nutrient'}),
        ChemotaxisModule('reaction.direct_bulk_hydrolysis', '直接产物释放 / Direct-bulk hydrolysis', 0, 'environment', {},
            {'released_amount': port('global.scalar', 'released_amount', 'molecule')},
            {'kcat_s': number('1/s'), 'contact_range_um': number('um')}, {},
            r'R_{if}=k_h E_{if}M_f/M_{f0},\quad U_f\le M_f',
            {'E_{if}': 'shared contacting enzyme copies', 'M_f': 'remaining material inventory', 'M_{f0}': 'frozen material initial_molecules'},
            'Source-B remaining-stock hydrolysis law with conservative shared contact allocation. The frozen initial material amount is an explicit global registry read. Soluble equivalents enter the external field, then uptake settles separately.',
            defaults={'kcat_s': 2., 'contact_range_um': .5}, evidence='reviewed source B direct-bulk active path'),
    ])
    modules.extend(_physiology_modules())
    for module in modules:
        if module.manifest['id'] == 'reaction.direct_bulk_hydrolysis':
            module.provides_roles = ['material.degradation']
            module.propose_degradation = direct_bulk_hydrolysis_adapter
    return ModuleRegistry(modules, execution_semantics=PROFILE)


def _physiology_modules():
    modules = []
    for suffix, title in [('monod', 'A Monod + yield'), ('yield', 'B yield-limited')]:
        modules.append(ChemotaxisModule('growth.nutrient_' + suffix, '营养生长 / ' + title, 8, 'population',
            {'accepted_amount': port('cell.scalar', 'accepted_amount', 'molecule', True)},
            {'intracellular_molecules': port('cell.scalar', 'intracellular_amount', 'molecule', True), 'volume': VOLUME,
             'used_molecules': port('cell.scalar', 'consumed_amount', 'molecule', True), 'growth_rate': GROWTH,
             'blocked': port('cell.scalar', 'growth_blocked', '1')},
            {'species': SPECIES, 'initial_molecules': number('molecule'), 'max_growth_per_min': number('1/min'),
             'volume_yield_um3_molecule': number('um^3/molecule'), **({'half_saturation_um': number('uM')} if suffix == 'monod' else {})},
            {'intracellular_molecules': ('cell.scalar', 'molecule'), 'volume': ('cell.scalar', 'um^3')},
            r'\Delta V=\min[\mu(C)V\Delta t,Y N],\quad N\leftarrow N-\Delta V/Y',
            {'V': 'cell volume [um^3]', 'N': 'available intracellular nutrient [molecule]', 'Y': 'parameters.volume_yield_um3_molecule'},
            'Source-specific growth. Accepted uptake enters the available pool; cumulative uptake is a separate statistic. Fixed-radius capsule geometry is checked before growth consumes nutrient. Blocked growth keeps its available nutrient.',
            defaults={'species': 'nutrient', 'initial_molecules': 0., 'max_growth_per_min': .1,
                'volume_yield_um3_molecule': .0001, **({'half_saturation_um': 1.} if suffix == 'monod' else {})}, evidence='source A/B reviewed growth'))
    modules.extend([
        ChemotaxisModule('expression.surface_copies', '表面表达 / Surface copy expression', 9, 'population',
            {'growth_rate': GROWTH, 'health': port('cell.scalar', 'health', '1'), 'surface_area': AREA}, {'enzyme_copies': ENZYME},
            {'policy': {'type': 'string'}, 'initial_copies': number('molecule'), 'synthesis_copies_min': number('molecule/min'), 'turnover_per_min': number('1/min'),
             'health_floor': number(maximum=1), 'health_hill': number(), 'metabolic_floor': number(maximum=1),
             'metabolic_half_growth_per_min': number('1/min'), 'burden_half_fraction': number(), 'burden_hill': number(),
             'enzyme_footprint_um2': number('um^2/molecule'), 'available_fraction': number(maximum=1)},
            {'enzyme_copies': ('cell.scalar', 'molecule')}, r'\dot N=s-k_{turnover}N',
            {'N': 'total copies, not concentration', 's': 'parameters.synthesis_copies_min', 'k_{turnover}': 'parameters.turnover_per_min'},
            'Total-copy expression with genuine turnover. The rebuilt policy gates synthesis by committed health, actual growth and surface occupancy; simplified uses explicit constant synthesis. Volume growth does not remove copies; division splits once.',
            defaults={'policy': 'simplified', 'initial_copies': 10., 'synthesis_copies_min': 0., 'turnover_per_min': 0.,
                'health_floor': .25, 'health_hill': 2., 'metabolic_floor': .6, 'metabolic_half_growth_per_min': .001,
                'burden_half_fraction': .35, 'burden_hill': 4., 'enzyme_footprint_um2': .001, 'available_fraction': .5}, evidence='reviewed count semantics; Lin & Amir 2018'),
        ChemotaxisModule('life.health_balance', '健康与死亡 / Health and death', 10, 'population',
            {'growth_rate': GROWTH, 'functional_copies': COPIES, 'enzyme_copies': ENZYME, 'surface_area': AREA},
            {'health': port('cell.scalar', 'health', '1'), 'death_hazard': port('cell.scalar', 'death_hazard', '1/min')},
            {'policy': {'type': 'string'}, 'initial_health': number(maximum=1), 'repair_per_min': number('1/min'),
             'burden_per_min': number('1/min'), 'starvation_per_min': number('1/min'), 'max_growth_per_min': number('1/min'),
             'death_max_per_min': number('1/min'), 'death_threshold': number(maximum=1), 'reference_pts_copies': number('molecule'),
             'carrier_footprint_um2': number('um^2/molecule'), 'enzyme_footprint_um2': number('um^2/molecule'),
             'available_fraction': number(maximum=1), **_rebuilt_health_parameters()}, {'health': ('cell.scalar', '1')},
            r'\dot H=R(\mu)(1-H)-T(\phi)-S(\mu),\quad P_{death}=1-e^{-h(H)\Delta t}',
            {'H': 'outputs.health', '\\mu': 'inputs.growth_rate [1/min]', '\\phi': 'explicit source-specific occupancy'},
            'Phenomenological source health rule. Biological deaths transfer unused intracellular nutrient to an explicit removed-residual ledger. Resource limits and numerical errors never create death events.',
            defaults={'policy': 'simplified', 'initial_health': 1., 'repair_per_min': .1, 'burden_per_min': .1,
                'starvation_per_min': .1, 'max_growth_per_min': .1, 'death_max_per_min': 1., 'death_threshold': .2,
                'reference_pts_copies': 100., 'carrier_footprint_um2': .001, 'enzyme_footprint_um2': .001, 'available_fraction': .5,
                **REBUILT_HEALTH_DEFAULTS}),
        ChemotaxisModule('division.area_adder', '面积 adder 分裂 / Area adder division', 11, 'population',
            {'volume': VOLUME}, {'divide': port('cell.boolean', 'division', '1'), 'birth_area': port('cell.scalar', 'surface_area', 'um^2'),
                'blocked': port('cell.scalar', 'division_blocked', '1')},
            {'added_area_um2': number('um^2'), 'minimum_fraction': number(maximum=.5), 'maximum_fraction': number(maximum=1)},
            {'birth_area': ('cell.scalar', 'um^2')}, r'A-A_{birth}\ge\Delta A,\quad V_1=fV,\; V_2=(1-f)V',
            {'A': 'fixed-radius capsule surface [um^2]', 'f': 'bounded division volume fraction'},
            'Area adder with explicit bounded random volume split. Both daughter capsules must fit without overlap. Failure postpones division, conserving all counts and nutrient. Membrane material is not modeled.',
            defaults={'added_area_um2': 2., 'minimum_fraction': .45, 'maximum_fraction': .55}, evidence='source B area adder; exploratory threshold'),
    ])
    for module in modules:
        for name in ('intracellular_molecules', 'enzyme_copies', 'volume'):
            if name in module.manifest['state']:
                module.manifest['state'][name]['on_division'] = 'split'
    return modules


REBUILT_HEALTH_DEFAULTS = {'repair_max_per_min': .01, 'repair_half_growth_per_min': .001,
    'tolerated_effective_occupancy': .25, 'occupancy_half_excess': .05, 'occupancy_hill': 3.,
    'occupancy_toxicity_max_per_min': .2, 'excess_toxicity_max_per_min': .03, 'excess_half_copies': 50000.,
    'starvation_max_per_min': .002, 'starvation_half_growth_per_min': .0005, 'starvation_hill': 4.,
    'death_half_health': .05, 'death_hazard_max_per_min': 1., 'death_hill': 4., 'inp_toxicity_weight': .15}


def _rebuilt_health_parameters():
    return {key: number('1/min' if key.endswith('_per_min') else 'molecule' if key.endswith('_copies') else '1')
            for key in REBUILT_HEALTH_DEFAULTS}
