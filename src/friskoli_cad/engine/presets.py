"""First-release presets built from ordinary module contracts.

The builders specify graphs and parameters; the runtime has no case dispatch.
All center-substrate values are constructed research settings, not calibration.
"""
from copy import deepcopy
from importlib.resources import files
import json
import math
import numpy as np
from friskoli_cad.science import pts as _pts
from friskoli_cad.science.pts_methylation import self_consistent_motor_half
from .science_extensions import modular_registry

TABLE = json.loads(files('friskoli_cad.science').joinpath('data', 'chip_parameters.json').read_text(encoding='utf-8'))['parameters']
GRAPH_KIND = {'measured': 'measurement', 'literature': 'literature', 'fitted': 'calibration', 'assumed': 'example'}
PROJECT_KIND = {'measured': 'measured', 'literature': 'reference', 'fitted': 'estimated', 'assumed': 'example'}

def provenance(key, overrides, kinds):
    if key in overrides:
        return {"kind": "user" if kinds is GRAPH_KIND else "example", "reference": "command-line override"}
    row = TABLE[key]
    return {"kind": kinds[row["category"]], "reference": row["source"]}


def build_chip_project(condition, seed, overrides=None):
    """Chip gradient along the horizontal short X axis, or a uniform control."""
    if condition not in ('gradient', 'zero'):
        raise ValueError("Chip condition must be 'gradient' or 'zero'")
    overrides = overrides or {}
    if set(overrides) - set(TABLE):
        raise ValueError('Unknown chip parameter override')
    v = {k: overrides.get(k, row["value"]) for k, row in TABLE.items()}
    registry = modular_registry()
    low, high = (v["low_uM"], v["high_uM"]) if condition == "gradient" else (v["background_uM"],) * 2
    length, width, height, step = v["length_um"], v["width_um"], v["height_um"], v["grid_spacing_um"]
    if step <= 0 or any(x <= 0 or abs(x / step - round(x / step)) > 1e-10 for x in (length, width, height)):
        raise ValueError('Grid spacing must divide each positive chip extent')
    counts = [int(round(x / step)) for x in (length, width, height)]
    spacing = [step, step, step]
    slope = (high - low) / length

    def node(nid, module_id, version, owner_kind, params):
        manifest = registry.get(module_id, version).manifest
        parameters = {}
        for name, (value, key) in params.items():
            entry = {"value": value, "provenance": provenance(key, overrides, GRAPH_KIND)}
            if "unit" in manifest["parameters"][name]:
                entry["unit"] = manifest["parameters"][name]["unit"]
            parameters[name] = entry
        owner = {"kind": owner_kind, "id": "cells" if owner_kind == "population" else nid}
        return {"id": nid, "module_id": module_id, "module_version": manifest["version"], "owner": owner, "parameters": parameters}

    def clamp(nid, x0, x1, target, target_key):
        return node(nid, "field.boundary_exchange", "1.0.0", "environment", {
            "species": ("ligand", "low_uM"), "lower_um": ([x0, 0.0, 0.0], "boundary_rate_s"), "upper_um": ([x1, width, height], "boundary_rate_s"),
            "target_um": (target, target_key), "rate_s": (v["boundary_rate_s"], "boundary_rate_s")})

    chip_chey0 = (v["chey_total_um"] * v["baseline_activity"] * v["chea_total_um"] * v["chey_phos_per_um_s"]
                 / (v["chey_phos_per_um_s"] * v["chea_total_um"] * v["baseline_activity"] + v["chey_dephos_s"]))
    nodes = [
        node("ligand_field", "field.diffusive_local", "2.0.0", "environment", {
            "species": ("ligand", "low_uM"), "diffusivity_um2_s": (v["diffusivity_um2_s"], "diffusivity_um2_s"),
            "gradient_x_um_per_um": (slope, "high_uM"), "gradient_y_um_per_um": (0.0, "low_uM"), "gradient_z_um_per_um": (0.0, "low_uM")}),
        clamp("low_side", 0.0, spacing[0], low, "low_uM" if condition == "gradient" else "background_uM"),
        clamp("high_side", length - spacing[0], length, high, "high_uM" if condition == "gradient" else "background_uM"),
        node("ligand_sample", "field.sample_local", "1.0.0", "population", {"species": ("ligand", "low_uM")}),
        node("motor_signal", "signal.mcp_adaptation", "2.0.0", "population", {
            "species": ("ligand", "low_uM"), "cluster_size": (v["cluster_size"], "cluster_size"),
            "inactive_binding_um": (v["inactive_binding_um"], "inactive_binding_um"), "active_binding_um": (v["active_binding_um"], "active_binding_um"),
            "methylation_energy": (v["methylation_energy"], "methylation_energy"), "methylation_reference": (v["methylation_reference"], "methylation_reference"),
            "adaptation_rate_s": (v["adaptation_rate_s"], "adaptation_rate_s"), "baseline_activity": (v["baseline_activity"], "baseline_activity"),
            "chea_total_um": (v["chea_total_um"], "chea_total_um"), "chey_total_um": (v["chey_total_um"], "chey_total_um"),
            "chey_phos_per_um_s": (v["chey_phos_per_um_s"], "chey_phos_per_um_s"), "chey_dephos_s": (v["chey_dephos_s"], "chey_dephos_s"),
            "motor_hill": (v["motor_hill"], "motor_hill"),
            "motor_half_um": (self_consistent_motor_half(chip_chey0, v["baseline_activity"], v["motor_hill"]), "baseline_activity"),
            "initial_chey_p_um": (chip_chey0, "baseline_activity")}),
        node("motility", "motion.hazard_run_tumble", "1.0.0", "population", {
            "tumble_mode": (v["tumble_mode"], "tumble_mode"), "turn_kernel": (v["turn_kernel"], "turn_kernel"), "speed_um_s": (v["speed_um_s"], "speed_um_s"),
            "minimum_tumble_rate_s": (v["minimum_tumble_rate_s"], "minimum_tumble_rate_s"), "maximum_tumble_rate_s": (v["maximum_tumble_rate_s"], "maximum_tumble_rate_s"),
            "tumble_duration_s": (v["tumble_duration_s"], "tumble_duration_s")}),
    ]
    by_id = {n["id"]: n for n in nodes}

    def edge(a, port_a, b, port_b, timing):
        return {"id": f"{a}_{port_a}_to_{b}_{port_b}", "from": {"node": a, "port": port_a}, "to": {"node": b, "port": port_b}, "timing": timing}
    edges = [edge("ligand_field", "concentration", "ligand_sample", "field", "previous_step"),
             edge("motility", "position", "ligand_sample", "position", "previous_step"),
             edge("ligand_sample", "concentration", "motor_signal", "concentration", "same_step"),
             edge("motor_signal", "motor_bias", "motility", "motor_bias", "previous_step")]
    channels = {}
    for nid, port in (("ligand_sample", "concentration"), ("motor_signal", "motor_bias"), ("motor_signal", "chey_p"), ("motility", "blocked"), ("motility", "turns")):
        manifest = registry.get(by_id[nid]["module_id"], by_id[nid]["module_version"]).manifest
        spec = manifest["outputs"][port]
        channels[f"{nid}.{port}"] = {"node": nid, "port": port, "group_id": "cells", "shape": spec["shape"], "quantity": spec["quantity"], "unit": spec["unit"]}

    rng = np.random.default_rng(seed)
    margin, gap, points = 3.0, 3.0, []
    attempts = 0
    while len(points) < v["n_cells"]:
        attempts += 1
        if attempts > max(1000, int(v['n_cells']) * 1000):
            raise ValueError('Could not place the requested cells without overlap')
        c = np.array([rng.uniform(margin, length - margin), rng.uniform(margin, width - margin), rng.uniform(2.0, height - 2.0)])
        if all(np.linalg.norm(c - q) >= gap for q in points):
            points.append(c)
    quats = rng.normal(size=(len(points), 4))
    quats /= np.linalg.norm(quats, axis=1, keepdims=True)
    geometry = {"shape": "capsule", "length_um": v["cell_length_um"], "diameter_um": v["cell_diameter_um"],
                "provenance": provenance("cell_length_um", overrides, PROJECT_KIND)}
    name = f"chip-mcp-{condition}"
    return {
        "project_version": "0.6.0", "execution_profile": "modular-spatial-v1", "random_seed": seed, "id": name,
        "domain": {"geometry": "volume", "counts_xyz": counts, "spacing_um_xyz": spacing},
        "species": {"ligand": {"concentration_unit": "uM", "initial_concentration": {
            "value": (low + high) / 2, "unit": "uM", "provenance": provenance("low_uM", overrides, PROJECT_KIND)}}},
        "groups": {"cells": {"ids": [f"cell-{i:04d}" for i in range(len(points))], "positions_um": [p.tolist() for p in points],
                             "orientation_xyzw": quats.tolist(), "initial_geometry": [dict(geometry) for _ in points]}},
        "controls": {},
        "graph": {"protocol_version": "0.2.0", "id": f"{name}-graph", "nodes": nodes, "edges": edges},
        "run": {"protocol_version": "0.1.0", "run_id": f"{name}-run", "graph_id": f"{name}-graph", "groups": ["cells"], "channels": channels},
        "observation": {"id": "high_half", "label": "High-concentration half (+X)", "axis": 0,
                        "region_lower_um": [length / 2, 0.0, 0.0], "region_upper_um": [length, width, height]},
    }


# Surface display level and per-enzyme turnover: (copies per cell, kcat 1/s).
# Only the product sets the release rate; (4000, 130), (200, 2600) and (400, 1300)
# give bit-identical fields, so the factorisation is chosen for plausibility, not
# for effect. 200 copies is a realistic level for a single-gene outer-membrane
# display construct. 130/s suits a glucosidase acting on a soluble substrate,
# which is what material.degradable_box represents; it is NOT a processive
# cellulase on solid cellulose, whose turnover is 0.01-1/s. The weak arm keeps
# the copy number and drops the turnover instead, so both arms share one construct.
RELEASE = {'baseline': (200., 2.5), 'strong': (200., 130.)}

# The center study is an open system: released product leaves through the domain
# boundary instead of accumulating in a closed box. Without this the cells' own
# release fills the domain to a flat plateau within ~60 s and the gradient the
# cells could follow disappears. Six non-overlapping slabs partition the boundary
# shell exactly once; rate_s is the same first-order relaxation the chip uses.
BOUNDARY_SINK = {'thickness_um': 4., 'rate_s': 5., 'target_um': 0.}

# The substrate is a cube centred on the domain, scaled as a whole rather than
# stretched per axis. 24 um in the 64 x 64 x 32 um small domain is a 3x scale of
# the original 8 um block and exactly fills the interior left by the 4 um sinks.
SUBSTRATE_EDGE_UM = 24.

# Literature values and physical bounds only. Nothing here is a gain: the two
# activity couplings, the sensor working point, the CheY cycle rates and the
# initial state are all derived from these by friskoli_cad.science.pts_methylation.
SIGNAL_CONSTANTS = {
    'baseline_activity': 1. / 3.,   # motor CW bias at the adapted state; NModel uses 0.35
    'motor_hill': 10.3,             # flagellar motor Hill coefficient (Cluzel et al. 2000)
    'methylation_min': 0.,          # unmethylated receptor
    'methylation_max': 4.,          # MCP methylation sites
    'tau_methylation_s': 4.,        # methylation adaptation time (Barkai & Leibler 1997)
    'tau_chey_s': .1,               # CheY-P response time
    'chea_total_uM': 1.,            # assumed scale for the sensory complex
    'chey_total_uM': 10.,           # ~8000 CheY per cell in ~1 fL
    'ei_rephos_s': 1.,              # EI autophosphorylation relaxation
}


def pre_adapted_signal_state(constants, ambient_uM, functional_copies, turnover_s, half_saturation_um):
    """Complete signal parameter set for cells already adapted to an ambient sugar level.

    Every entry is derived rather than chosen:
      ei_dephos   EI half-dephosphorylated exactly where PTS uptake is half-saturated
      e0          e = J*k/(J*k + k_rephos), with J the uptake flux at the ambient
      m0          activity(e0, m0) == a0, so the run starts adapted
      chey rates  balanced at a0, with response time tau_chey_s
      y0          half of CheY phosphorylated, the balanced steady state
      motor_half  bias(y0) == a0 (criterion 0)
    Starting from the zero-flux state instead leaves a loading transient inside every run.
    """
    from friskoli_cad.science import pts as _pts
    from friskoli_cad.science import pts_methylation as _pm
    flux = float(np.asarray(_pts.pts_request(np.array([ambient_uM]), np.array([functional_copies]),
                                             turnover_s, half_saturation_um)).ravel()[0])
    dephos = float(np.asarray(_pm.ei_dephos_per_molecule(
        constants['ei_rephos_s'], functional_copies, turnover_s)).ravel()[0])
    on = flux * dephos
    e0 = on / (on + constants['ei_rephos_s']) if on + constants['ei_rephos_s'] > 0 else 0.
    phos, chey_dephos = _pm.chey_rates(constants['tau_chey_s'], constants['chey_total_uM'],
                                       constants['baseline_activity'], constants['chea_total_uM'])
    y0 = constants['chey_total_uM'] / 2.
    rate = float(np.asarray(_pm.methylation_rate(
        constants['tau_methylation_s'], constants['methylation_min'],
        constants['methylation_max'])).ravel()[0])
    derived = {**constants, 'ei_dephos_per_molecule': dephos, 'initial_ei_fraction': e0,
               'adaptation_rate_s': rate,
               'initial_chey_p_um': y0,
               'chey_phos_per_um_s': float(phos), 'chey_dephos_s': float(chey_dephos),
               'motor_half_um': 1.}
    probe = _pm.PTSMethylationParameters(ei_rephos_s=derived['ei_rephos_s'],
        methylation_min=derived['methylation_min'], methylation_max=derived['methylation_max'],
        adaptation_rate_s=derived['adaptation_rate_s'], baseline_activity=derived['baseline_activity'],
        chea_total_uM=derived['chea_total_uM'], chey_total_uM=derived['chey_total_uM'],
        motor_hill=derived['motor_hill'], motor_half_uM=derived['motor_half_um'],
        ei_dephos_per_molecule=derived['ei_dephos_per_molecule'],
        chey_phos_per_uM_s=derived['chey_phos_per_um_s'], chey_dephos_s=derived['chey_dephos_s'])
    derived['initial_methylation'] = float(np.asarray(_pm.adapted_methylation(e0, probe)).ravel()[0])
    derived['motor_half_um'] = float(np.asarray(_pm.self_consistent_motor_half(
        y0, constants['baseline_activity'], constants['motor_hill'])).ravel()[0])
    # Node parameter names are the lowercased dataclass fields; the module adapter
    # maps them back, so the two spellings must stay in step.
    return {k.lower(): v for k, v in {
        'ei_rephos_s': ('EI autophosphorylation relaxation', derived['ei_rephos_s']),
        'ei_dephos_per_molecule': ('EI half-dephosphorylated where PTS uptake is half-saturated',
                                   derived['ei_dephos_per_molecule']),
        'methylation_min': ('unmethylated receptor', derived['methylation_min']),
        'methylation_max': ('MCP methylation sites', derived['methylation_max']),
        'adaptation_rate_s': ('1/(tau_Methylation * methylation gain), tau_M = 4 s',
                              derived['adaptation_rate_s']),
        'baseline_activity': ('motor CW bias at the adapted state (Cluzel 2000)', derived['baseline_activity']),
        'chea_total_uM': ('assumed scale for the sensory complex', derived['chea_total_uM']),
        'chey_total_uM': ('~8000 CheY per cell in ~1 fL', derived['chey_total_uM']),
        'chey_phos_per_uM_s': ('CheY phosphorylation, balanced at baseline with tau = 0.1 s',
                               derived['chey_phos_per_um_s']),
        'chey_dephos_s': ('CheY dephosphorylation, balanced at baseline with tau = 0.1 s',
                          derived['chey_dephos_s']),
        'motor_hill': ('flagellar motor Hill coefficient (Cluzel 2000)', derived['motor_hill']),
        'motor_half_um': ('criterion 0: bias(y0) == baseline_activity', derived['motor_half_um']),
        'initial_ei_fraction': (f'fixed point of the ambient {ambient_uM:.4g} uM',
                                derived['initial_ei_fraction']),
        'initial_methylation': (f'adapted to the ambient {ambient_uM:.4g} uM',
                                derived['initial_methylation']),
        'initial_chey_p_um': ('half of CheY phosphorylated at the adapted state',
                              derived['initial_chey_p_um']),
    }.items()}


def _boundary_slabs(extent, thickness):
    """Six slabs covering the boundary shell once, with no overlapping corners."""
    t = float(thickness)
    if not 0. < 2. * t < float(np.min(extent)):
        raise ValueError('boundary sink thickness must leave an interior region')
    x, y, z = (float(v) for v in extent)
    return [
        ('x', 'lo', [0., 0., 0.], [t, y, z]),
        ('x', 'hi', [x - t, 0., 0.], [x, y, z]),
        ('y', 'lo', [t, 0., 0.], [x - t, t, z]),
        ('y', 'hi', [t, y - t, 0.], [x - t, y, z]),
        ('z', 'lo', [t, t, 0.], [x - t, y - t, t]),
        ('z', 'hi', [t, t, z - t], [x - t, y - t, z]),
    ]


def build_center_project(mechanism='a', scale='small', *, feedback=True, seed=1, spacing_um=1., release='baseline'):
    """Matched square-domain PTS/surface-enzyme study and feedback-disabled control."""
    if release not in RELEASE:
        raise ValueError('Unknown center-study release setting')
    if mechanism not in ('a', 'b') or scale not in ('small', 'medium'):
        raise ValueError('Center study requires mechanism a/b and scale small/medium')
    registry = modular_registry()
    factor = 1. if scale == 'small' else 2.
    extent = np.array([64., 64., 32.]) * factor
    counts = extent / spacing_um
    if spacing_um <= 0 or not np.equal(counts, np.rint(counts)).all():
        raise ValueError('Grid spacing must divide each study extent')
    counts = counts.astype(int)
    center = extent / 2
    lower, upper = center - SUBSTRATE_EDGE_UM * factor / 2, center + SUBSTRATE_EDGE_UM * factor / 2
    reference = 'docs/first-release/science.md: constructed center-substrate study; no parameter fitting'
    def node(nid, mid, params, version='1.0.0', population=True):
        m = registry.get(mid, version).manifest
        return {'id': nid, 'module_id': mid, 'module_version': version,
            'owner': {'kind': 'population' if population else 'environment', 'id': 'cells' if population else nid},
            'parameters': {k: {'value': v, **({'unit': m['parameters'][k]['unit']} if 'unit' in m['parameters'][k] else {}),
                'provenance': {'kind': 'example', 'reference': reference}} for k, v in params.items()}}
    capacity = {'g_requested':2., 'reference_pts_copies':100.}
    capacity.update({'basal_inner_fraction':.5, 'pts_max_available_fraction':.4, 'ascf_area_um2':.001} if mechanism == 'a'
                    else {'g_cap':3., 'reference_area_um2':5.})
    # Fixed initial extracellular product seed. It is initial stock and is
    # accounted separately from enzyme-released equivalents.
    z, y, x = np.indices(tuple(counts[::-1]))
    positions = np.stack([(x+.5)*spacing_um, (y+.5)*spacing_um, (z+.5)*spacing_um], axis=-1)
    values = np.exp(-np.sum((positions-center)**2, axis=-1)/(2*(8.*factor)**2))
    blocked = np.all((positions >= lower) & (positions < upper), axis=-1)
    values[blocked] = 0.
    points = [[float(center[0]+20.*factor*math.cos(2*math.pi*i/16)), float(center[1]+20.*factor*math.sin(2*math.pi*i/16)), float(h)]
              for h in [extent[2]/4,extent[2]/2,3*extent[2]/4] for i in range(16)]
    # Cells start where the initial field already stands, so their internal state must be the
    # fixed point of that background rather than the zero-flux state.
    _ambient = float(np.mean([values[int(min(p_[2]/spacing_um, values.shape[0]-1)),
                                      int(min(p_[1]/spacing_um, values.shape[1]-1)),
                                      int(min(p_[0]/spacing_um, values.shape[2]-1))] for p_ in points]))
    _area = _pts.capsule_area_um2(2., .8)
    if mechanism == 'a':
        _cap = _pts.rebuilt_capacity(_area, capacity['g_requested'], _pts.RebuiltCapacityParameters(
            capacity['reference_pts_copies'], capacity['basal_inner_fraction'],
            capacity['pts_max_available_fraction'], capacity['ascf_area_um2']))
    else:
        _cap = _pts.simplified_capacity(_area, capacity['g_requested'], _pts.SimplifiedCapacityParameters(
            capacity['reference_pts_copies'], capacity['g_cap'], capacity['reference_area_um2']))
    _copies = float(np.asarray(_cap.functional_copies).ravel()[0])
    derived = pre_adapted_signal_state(SIGNAL_CONSTANTS, _ambient, _copies, 2., 1.)
    signal = {'species': 'sugar', **{k: v for k, (_, v) in derived.items()}}
    nodes = [
        node('sugar_field', 'field.diffusive_local', {'species':'sugar', 'diffusivity_um2_s':10.,
            'gradient_x_um_per_um':0., 'gradient_y_um_per_um':0., 'gradient_z_um_per_um':0.}, '2.0.0', False),
        node('initial_product', 'field.initial_array', {'species':'sugar', 'values_um':values.tolist()}, population=False),
        node('cell_area', 'pts.capsule_area', {}),
        node('capacity', 'pts.capacity_rebuilt' if mechanism == 'a' else 'pts.capacity_simplified', capacity),
        node('sugar_sample', 'field.sample_local', {'species':'sugar'}),
        node('uptake_request', 'uptake.pts_request', {'species':'sugar', 'turnover_s':2., 'half_saturation_um':1.}),
        node('accepted_uptake', 'uptake.local_settlement', {'species':'sugar', 'initial_molecules':0.}),
        node('pts_signal', 'signal.pts_methylation', signal),
        node('motility', 'motion.hazard_run_tumble', {'tumble_mode':'instant', 'turn_kernel':'isotropic', 'speed_um_s':20.,
            'minimum_tumble_rate_s':.1, 'maximum_tumble_rate_s':3., 'tumble_duration_s':.1}),
        node('surface_enzyme', 'surface.enzyme_activity', {'enzyme_copies':RELEASE[release][0]}),
        node('central_substrate', 'material.degradable_box', {'species':'sugar',
            **{f'{side}_{axis}_um':float(value[i]) for side, value in [('lower',lower),('upper',upper)] for i, axis in enumerate('xyz')},
            'initial_molecules':100000000.}, population=False),
        node('contact_hydrolysis', 'reaction.contact_degradation', {'kcat_s':RELEASE[release][1], 'contact_range_um':.5}, population=False),
        *[node(f'sink_{axis}_{side}', 'field.boundary_exchange',
               {'species':'sugar', 'lower_um':lower_um, 'upper_um':upper_um,
                'target_um':BOUNDARY_SINK['target_um'], 'rate_s':BOUNDARY_SINK['rate_s']}, population=False)
          for axis, side, lower_um, upper_um in _boundary_slabs(extent, BOUNDARY_SINK['thickness_um'])],
        node('reserve', 'metabolism.reserve_balance', {'species':'sugar', 'initial_molecules':1000000., 'maintenance_molecules_s':10.}),
    ]
    if not feedback:
        # Match each mechanism's unstimulated readout, keeping the feedback
        # modules active for measurements. Only the motor-to-motion edge changes.
        baseline_y = signal['initial_chey_p_um']
        bias = baseline_y**signal['motor_hill']/(signal['motor_half_um']**signal['motor_hill'] + baseline_y**signal['motor_hill'])
        nodes.append(node('constant_motor', 'signal.constant_bias', {'bias':bias}))
    def edge(a, pa, b, pb, timing='same_step'):
        return {'id':f'{a}_{pa}_to_{b}_{pb}', 'from':{'node':a,'port':pa}, 'to':{'node':b,'port':pb}, 'timing':timing}
    edges = [edge('cell_area','surface_area','capacity','surface_area'),
        edge('sugar_field','concentration','sugar_sample','field','previous_step'),
        edge('motility','position','sugar_sample','position','previous_step'),
        edge('sugar_sample','concentration','uptake_request','concentration'),
        edge('capacity','functional_copies','uptake_request','functional_copies'),
        edge('sugar_field','concentration','accepted_uptake','field','previous_step'),
        edge('uptake_request','requested_flux','accepted_uptake','requested_flux'),
        edge('accepted_uptake','accepted_flux','pts_signal','accepted_flux'),
        edge('accepted_uptake','accepted_amount','reserve','accepted_amount'),
        edge('pts_signal' if feedback else 'constant_motor','motor_bias','motility','motor_bias','previous_step')]
    channels = {}
    by_id = {n['id']:n for n in nodes}
    for nid, port in [('sugar_sample','concentration'),('accepted_uptake','accepted_amount'),('accepted_uptake','cumulative_uptake'),
                      ('pts_signal','chey_p'),('pts_signal','motor_bias'),('pts_signal','methylation'),('pts_signal','activity'),('pts_signal','ei_fraction'),('surface_enzyme','enzyme_copies'),
                      ('reserve','intracellular_molecules'),('motility','blocked')]:
        n = by_id[nid]; p = registry.get(n['module_id'],n['module_version']).manifest['outputs'][port]
        channels[nid+'.'+port] = {'node':nid,'port':port,'group_id':'cells',**{k:p[k] for k in ('shape','quantity','unit')}}
    for _n in nodes:
        if _n['id'] == 'pts_signal':
            for _k, (_why, _) in derived.items():
                _n['parameters'][_k]['provenance'] = {'kind': 'example',
                    'reference': _why + '; derived by friskoli_cad.science.pts_methylation'}
        # The release rate is the product of these two; the split is stated
        # explicitly because only the product is observable in the model.
        if _n['id'] == 'surface_enzyme':
            _n['parameters']['enzyme_copies']['provenance'] = {'kind': 'example',
                'reference': 'single-gene outer-membrane display level; release rate is cuts x kcat, '
                             'so the copy number is chosen for plausibility, not for effect'}
        if _n['id'] == 'contact_hydrolysis':
            _n['parameters']['kcat_s']['provenance'] = {'kind': 'example',
                'reference': 'per-enzyme turnover on a soluble substrate; a processive cellulase on '
                             'solid cellulose turns over at 0.01-1/s, which is the weak arm'}
    rng = np.random.default_rng(seed)
    quats = rng.normal(size=(len(points),4)); quats /= np.linalg.norm(quats,axis=1,keepdims=True)
    name = f'center-pts-{mechanism}-{scale}' + ('' if release == 'baseline' else '-' + release) + ('' if feedback else '-control')
    geom = {'shape':'capsule','length_um':2.,'diameter_um':.8,'provenance':{'kind':'example','reference':reference}}
    return {'project_version':'0.6.0','execution_profile':'modular-spatial-v1','random_seed':seed,'id':name,
        'domain':{'geometry':'volume','counts_xyz':counts.tolist(),'spacing_um_xyz':[spacing_um]*3},
        'species':{'sugar':{'concentration_unit':'uM','initial_concentration':{'value':0.,'unit':'uM','provenance':{'kind':'example','reference':reference}}}},
        'groups':{'cells':{'ids':[f'cell-{i:04d}' for i in range(len(points))], 'positions_um':points,
            'orientation_xyzw':quats.tolist(),'initial_geometry':[deepcopy(geom) for _ in points]}},'controls':{},
        'graph':{'protocol_version':'0.2.0','id':name+'-graph','nodes':nodes,'edges':edges},
        'run':{'protocol_version':'0.1.0','run_id':name+'-run','graph_id':name+'-graph','groups':['cells'],'channels':channels},
        'observation':{'id':'center_region','label':'Center-substrate neighborhood','axis':0,
            'region_lower_um':(center-8.*factor).tolist(),'region_upper_um':(center+8.*factor).tolist(),
            'radial_center_um':center.tolist(),'radial_radii_um':[8.*factor,12.*factor,16.*factor]}}


EXAMPLES = {
    'modular-foundation': ('模块基础 / Modular foundation', 'Field, uptake, reserve, death, motion and explicit sources.'),
    'modular-material': ('材料与生命周期 / Material and lifecycle', 'Cellulose stoichiometry, catalyst providers, erosion and lineage.'),
    **{f'chip-mcp-{c}': ('MCP 梯度芯片 / MCP gradient chip' if c == 'gradient' else 'MCP 零梯度芯片 / MCP zero-gradient chip',
         'Wild-type MCP–MeAsp benchmark along the short horizontal X axis; finite boundary relaxation; constructed parameters.') for c in ('gradient','zero')},
    **{f'center-pts-{m}-small-strong'+('-control' if not f else ''):
        (f'PTS {m.upper()} · 小域 · 强释放'+(' · 无反馈对照' if not f else ''),
         'Strong-release center-substrate study with the responsive PTS methylation profile; 4000 surface enzymes '
         'at kcat 130/s and a 1 um grid resolve the interfacial concentration layer. Exploratory constructed setting, not calibrated.')
       for m in ('a','b') for f in (True, False)},
}


def make_example(name='modular-foundation'):
    if name not in EXAMPLES:
        raise ValueError('Unknown first-release example')
    return json.loads(files('friskoli_cad').joinpath('examples', name.replace('-','_')+'.project.json').read_text(encoding='utf-8'))


def template_catalog():
    return [{'id':name,'version':'1.0.0','label':label,'description':description,'example_id':name,'maturity':'exploratory',
        'source':'docs/first-release/science.md; constructed research settings, not experimental validation',
        'module_keys':sorted({n['module_id']+'@'+n['module_version'] for n in make_example(name)['graph']['nodes']}),
        'recommended_settings':{'dt_s':.05,'steps':2400 if name.startswith(('chip-','center-')) else 40,'frame_every_steps':20 if name.startswith(('chip-','center-')) else 1,'include_fields':False if name.startswith(('chip-','center-')) else True}}
        for name,(label,description) in EXAMPLES.items()]
