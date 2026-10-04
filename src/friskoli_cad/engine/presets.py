"""First-release presets built from ordinary module contracts.

The builders specify graphs and parameters; the runtime has no case dispatch.
All center-substrate values are constructed research settings, not calibration.
"""
from copy import deepcopy
from importlib.resources import files
import json
import math
import numpy as np
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
            "motor_hill": (v["motor_hill"], "motor_hill"), "motor_half_um": (v["motor_half_um"], "motor_half_um"),
            "initial_chey_p_um": (v["chey_total_um"] * v["baseline_activity"] * v["chea_total_um"] * v["chey_phos_per_um_s"]
                                  / (v["chey_phos_per_um_s"] * v["chea_total_um"] * v["baseline_activity"] + v["chey_dephos_s"]), "baseline_activity")}),
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


def build_center_project(mechanism='a', scale='small', *, feedback=True, seed=1, spacing_um=4.):
    """Matched square-domain PTS/surface-enzyme study and feedback-disabled control."""
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
    lower, upper = center - 4. * factor, center + 4. * factor
    reference = 'docs/first-release/science.md: constructed center-substrate study; no parameter fitting'
    def node(nid, mid, params, version='1.0.0', population=True):
        m = registry.get(mid, version).manifest
        return {'id': nid, 'module_id': mid, 'module_version': version,
            'owner': {'kind': 'population' if population else 'environment', 'id': 'cells' if population else nid},
            'parameters': {k: {'value': v, **({'unit': m['parameters'][k]['unit']} if 'unit' in m['parameters'][k] else {}),
                'provenance': {'kind': 'example', 'reference': reference}} for k, v in params.items()}}
    signal = {'species':'sugar', 'ei_total_um':1., 'ei_dephos_per_molecule':.01, 'ei_rephos_s':1.,
        'ei_chea_inhibition_um':1., 'chea_total_um':1., 'chey_total_um':10., 'chey_phos_per_um_s':1.,
        'chey_dephos_s':1., 'motor_hill':4., 'motor_half_um':3.5, 'initial_ei_fraction':0., 'initial_chey_p_um':5.}
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
    nodes = [
        node('sugar_field', 'field.diffusive_local', {'species':'sugar', 'diffusivity_um2_s':10.,
            'gradient_x_um_per_um':0., 'gradient_y_um_per_um':0., 'gradient_z_um_per_um':0.}, '2.0.0', False),
        node('initial_product', 'field.initial_array', {'species':'sugar', 'values_um':values.tolist()}, population=False),
        node('cell_area', 'pts.capsule_area', {}),
        node('capacity', 'pts.capacity_rebuilt' if mechanism == 'a' else 'pts.capacity_simplified', capacity),
        node('sugar_sample', 'field.sample_local', {'species':'sugar'}),
        node('uptake_request', 'uptake.pts_request', {'species':'sugar', 'turnover_s':2., 'half_saturation_um':1.}),
        node('accepted_uptake', 'uptake.local_settlement', {'species':'sugar', 'initial_molecules':0.}),
        node('pts_signal', 'signal.pts_accepted', signal),
        node('memory_motor', 'signal.concentration_memory' if mechanism == 'a' else 'signal.chey_memory',
            {'species':'sugar', 'memory_tau_s':3., 'gradient_strength_per_um':2., 'initial_memory_um':0.} if mechanism == 'a' else
            {'adaptation_tau_s':3., 'baseline_um':3.5, 'total_um':10., 'motor_hill':8., 'motor_half_um':3.5, 'initial_memory_um':5.}),
        node('motility', 'motion.hazard_run_tumble', {'tumble_mode':'instant', 'turn_kernel':'isotropic', 'speed_um_s':20.,
            'minimum_tumble_rate_s':.1, 'maximum_tumble_rate_s':3., 'tumble_duration_s':.1}),
        node('surface_enzyme', 'surface.enzyme_activity', {'enzyme_copies':1000.}),
        node('central_substrate', 'material.degradable_box', {'species':'sugar',
            **{f'{side}_{axis}_um':float(value[i]) for side, value in [('lower',lower),('upper',upper)] for i, axis in enumerate('xyz')},
            'initial_molecules':100000000.}, population=False),
        node('contact_hydrolysis', 'reaction.contact_degradation', {'kcat_s':.5, 'contact_range_um':.5}, population=False),
        node('reserve', 'metabolism.reserve_balance', {'species':'sugar', 'initial_molecules':1000000., 'maintenance_molecules_s':10.}),
    ]
    if not feedback:
        # Match each mechanism's unstimulated readout, keeping the feedback
        # modules active for measurements. Only the motor-to-motion edge changes.
        bias = 5.**4/(3.5**4 + 5.**4) if mechanism == 'a' else .5
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
        edge('memory_motor' if feedback else 'constant_motor','motor_bias','motility','motor_bias','previous_step')]
    if mechanism == 'a':
        edges += [edge('sugar_sample','concentration','memory_motor','concentration'),edge('pts_signal','motor_bias','memory_motor','motor_bias')]
    else:
        edges += [edge('pts_signal','chey_p','memory_motor','chey_p')]
    channels = {}
    by_id = {n['id']:n for n in nodes}
    for nid, port in [('sugar_sample','concentration'),('accepted_uptake','accepted_amount'),('accepted_uptake','cumulative_uptake'),
                      ('pts_signal','chey_p'),('memory_motor','motor_bias'),('memory_motor','memory'),('surface_enzyme','enzyme_copies'),
                      ('reserve','intracellular_molecules'),('motility','blocked')]:
        n = by_id[nid]; p = registry.get(n['module_id'],n['module_version']).manifest['outputs'][port]
        channels[nid+'.'+port] = {'node':nid,'port':port,'group_id':'cells',**{k:p[k] for k in ('shape','quantity','unit')}}
    points = [[float(center[0]+20.*factor*math.cos(2*math.pi*i/16)), float(center[1]+20.*factor*math.sin(2*math.pi*i/16)), float(h)]
              for h in [extent[2]/4,extent[2]/2,3*extent[2]/4] for i in range(16)]
    rng = np.random.default_rng(seed)
    quats = rng.normal(size=(len(points),4)); quats /= np.linalg.norm(quats,axis=1,keepdims=True)
    name = f'center-pts-{mechanism}-{scale}' + ('' if feedback else '-control')
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
    **{f'center-pts-{m}-{s}'+('' if f else '-control'):
        (f'PTS {m.upper()} · '+('小域' if s=='small' else '中域')+(' · 无反馈对照' if not f else ''),
         'Square center-substrate engineering study; fixed surface enzymes; '+('PTS motor feedback.' if f else 'Matched control with constant motor feedback.'))
       for m in ('a','b') for s in ('small','medium') for f in (True,False)}
}


def make_example(name='modular-foundation'):
    if name not in EXAMPLES:
        raise ValueError('Unknown first-release example')
    return json.loads(files('friskoli_cad').joinpath('examples', name.replace('-','_')+'.project.json').read_text(encoding='utf-8'))


def template_catalog():
    return [{'id':name,'version':'1.0.0','label':label,'description':description,'example_id':name,'maturity':'exploratory',
        'source':'docs/first-release/science.md; constructed research settings, not experimental validation',
        'module_keys':sorted({n['module_id']+'@'+n['module_version'] for n in make_example(name)['graph']['nodes']}),
        'recommended_settings':{'dt_s':.1,'steps':1200 if name.startswith(('chip-','center-')) else 20,'frame_every_steps':10 if name.startswith(('chip-','center-')) else 1,'include_fields':False if name.startswith(('chip-','center-')) else True}}
        for name,(label,description) in EXAMPLES.items()]
