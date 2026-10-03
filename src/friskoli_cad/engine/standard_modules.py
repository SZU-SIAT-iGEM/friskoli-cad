"""Explicit numerical conversions, entity mappings and matched grid operators."""
from copy import deepcopy
import numpy as np

from friskoli_cad.science.processes import matched_trilinear_weights
from .module_api import ModuleProposal
from .port_semantics import entity_ids_for_port
from .science_extensions import ScientificModule, array_parameter, RECORD_PORT, GRID_READS
from .pts_modules import port, SPECIES


def _unit_scale(factor):
    def calculate(context, initial):
        return ModuleProposal({'value': np.asarray(context.inputs['value'], dtype=np.float64) * factor}, {})
    return calculate


def _mapped(context, initial):
    source_ids = entity_ids_for_port({'entity_set': 'parameter:source_population'}, context)
    values = np.asarray(context.inputs['value'])
    if values.shape != (len(source_ids),):
        raise ValueError('Mapping input must follow the declared source IDs')
    source = dict(zip(source_ids, values))
    mapping = context.parameters['mapping']
    if not set(context.entity_ids) <= set(mapping):
        raise ValueError('Every living destination cell requires an explicit source ID mapping')
    try:
        output = np.asarray([source[mapping[cid]] for cid in context.entity_ids], dtype=np.float64)
    except KeyError as exc:
        raise ValueError('Mapping references a missing source cell; update the mapping after lifecycle changes') from exc
    return ModuleProposal({'value': output}, {})


def _aggregate(context, initial):
    values = np.asarray(context.inputs['value'], dtype=np.float64)
    source_ids = entity_ids_for_port({'entity_set': 'parameter:source_population'}, context)
    if values.shape != (len(source_ids),):
        raise ValueError('Aggregation input differs from its declared entity set')
    return ModuleProposal({'sum': float(values.sum()), 'statistics': {
        'count': len(source_ids), 'mean': float(values.mean()) if len(values) else None,
        'minimum': float(values.min()) if len(values) else None,
        'maximum': float(values.max()) if len(values) else None,
        'source_ids': source_ids}}, {})


def _weights(context):
    shape = tuple(context.world['grid_shape_zyx'])
    positions = np.asarray(context.world['positions_um']).reshape(-1, 3)
    spacing = np.asarray(context.world['spacing_xyz'])
    if np.any(positions < 0) or np.any(positions > np.asarray(shape[::-1]) * spacing):
        raise ValueError('Sampling positions must lie inside the physical domain')
    weights = matched_trilinear_weights(positions, shape, spacing)
    blocked = np.asarray(context.world['blocked']).reshape(-1)
    result = []
    for row in weights:
        allowed = {index: weight for index, weight in row.items() if weight > 0 and not blocked[index]}
        total = sum(allowed.values())
        if total <= 0:
            raise ValueError('A cell has no fluid support for the registered matched operator')
        result.append({index: weight / total for index, weight in allowed.items()})
    return result


def _sample(context, initial):
    field = np.asarray(context.inputs['field'])
    if field.shape != tuple(context.world['grid_shape_zyx']):
        raise ValueError('Sampler field shape differs from the physical grid')
    result = np.asarray([sum(field.flat[index] * weight for index, weight in row.items())
                         for row in _weights(context)], dtype=np.float64)
    return ModuleProposal({'concentration': result}, {})


def _deposit(context, initial):
    amounts = np.asarray(context.inputs['amount'], dtype=np.float64)
    if amounts.shape != (len(context.entity_ids),) or np.any(amounts < 0):
        raise ValueError('Deposition requires one nonnegative amount per cell')
    result = np.zeros(context.world['grid_shape_zyx'], dtype=np.float64)
    for amount, row in zip(amounts, _weights(context)):
        for index, weight in row.items():
            result.flat[index] += amount * weight
    return ModuleProposal({'amount': result}, {})


def standard_modules():
    modules = []
    conversions = [('concentration_um_to_mm', 'concentration', 'uM', 'mM', .001, True),
                   ('concentration_mm_to_um', 'concentration', 'mM', 'uM', 1000., True),
                   ('length_um_to_m', 'length', 'um', 'm', 1e-6, False),
                   ('length_m_to_um', 'length', 'm', 'um', 1e6, False),
                   ('time_s_to_h', 'elapsed_time', 's', 'h', 1 / 3600., False),
                   ('time_h_to_s', 'elapsed_time', 'h', 's', 3600., False)]
    for identifier, quantity, unit_in, unit_out, factor, species in conversions:
        for scope in ('population', 'environment'):
            shape = 'cell.scalar' if scope == 'population' else 'global.scalar'
            modules.append(ScientificModule('units.' + identifier + ('_cells' if scope == 'population' else ''),
                f'单位换算 / {unit_in} → {unit_out}', 'prepare', scope,
                {'value': port(shape, quantity, unit_in, species)}, {'value': port(shape, quantity, unit_out, species)},
                {'species': SPECIES} if species else {}, {}, f'y={factor!r}x',
                'Exact declared SI scaling, preserving quantity, species and entity order. No arbitrary calibration factor.', _unit_scale(factor)))
    for quantity, unit, species in [('scalar', '1', False), ('concentration', 'uM', True), ('amount', 'molecule', True)]:
        input_port = {**port('cell.scalar', quantity, unit, species), 'entity_set': 'parameter:source_population'}
        parameters = {'source_population': {'type': 'string'}, **({'species': SPECIES} if species else {})}
        modules.append(ScientificModule('mapping.' + quantity + '_by_id', '实体映射 / ' + quantity,
            'prepare', 'population', {'value': input_port}, {'value': port('cell.scalar', quantity, unit, species)},
            {**parameters, 'mapping': {'type': 'object', 'additionalProperties': {'type': 'string'}}}, {},
            r'y_{j}=x_{m(j)}', 'Explicit destination-ID to source-ID map. No equal-length inference or automatic cell-identity replacement.', _mapped))
        modules.append(ScientificModule('aggregate.' + quantity, '实体聚合 / ' + quantity,
            'observation', 'environment', {'value': deepcopy(input_port)},
            {'sum': port('global.scalar', quantity, unit, species), 'statistics': RECORD_PORT},
            parameters, {}, r'S=\sum_i x_i,\quad \bar{x}=S/n',
            'Aggregation over the explicitly named entity set. Empty mean/min/max are null; sum is zero. Records retain source IDs.', _aggregate))
    reads = (*GRID_READS, 'positions_um')
    modules.append(ScientificModule('field.sample_trilinear', '匹配三线性采样 / Matched trilinear sampling',
        'prepare', 'population', {'field': port('field.scalar', 'concentration', 'uM', True)},
        {'concentration': port('cell.scalar', 'concentration', 'uM', True)}, {'species': SPECIES}, {},
        r'c_i=\sum_jw_{ij}C_j,\quad\sum_jw_{ij}=1',
        'Cell-centred trilinear weights; blocked support removed and renormalized. Same weights as deposit_trilinear.', _sample, reads=reads))
    modules.append(ScientificModule('field.deposit_trilinear', '匹配三线性沉积 / Matched trilinear deposition',
        'field', 'population', {'amount': port('cell.scalar', 'amount', 'molecule', True)},
        {'amount': port('field.scalar', 'amount', 'molecule', True)}, {'species': SPECIES}, {},
        r'N_j=\sum_iw_{ij}N_i,\quad\sum_jN_j=\sum_iN_i',
        'Pure amount mapping, adjoint to sample_trilinear. The output alone does not mutate field inventory; a declared reaction or source must own that transfer.', _deposit, reads=reads))
    return modules
