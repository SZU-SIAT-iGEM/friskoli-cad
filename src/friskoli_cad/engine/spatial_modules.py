"""Declarations for a spatial, unbiased motility reference profile."""
from copy import deepcopy

from .profiles import SPATIAL_PROFILE
from .pts_modules import PTSModule, pts_registry, port, number, SPECIES, REQUEST, ACCEPTED
from .runtime import ModuleRegistry
from .degradation import propose_contact_degradation

FIELD = port("field.scalar", "concentration", "uM", True)
SOURCE = port("global.scalar", "source_inventory", "molecule", True)
POSITION = port("cell.vector", "position", "um")


def contact_degradation_adapter(material, capsules, enzyme_copies, parameters, dt_s):
    """Registered executable adapter; alternatives must expose this pure contract."""
    return propose_contact_degradation(material, capsules, enzyme_copies,
        kcat_s=parameters['kcat_s'], contact_range_um=parameters['contact_range_um'], dt_s=dt_s)


class SpatialModule(PTSModule):
    def __init__(self, *args):
        super().__init__(*args)
        self.declaration["mathematics"].update(
            implementation="friskoli_cad.engine.spatial_modules.SpatialModule",
            verification={"status": "tested", "tests": ["tests/test_spatial_runtime.py"]},
            assumptions=["spatial-unbiased-v1; explicit exploratory parameters, not strain calibration.",
                "No chemotactic motor coupling, hydrodynamics, growth or lifecycle.",
                "Finite sources, voxel-aligned solid boxes and conservative blocked-motion policy."])


def spatial_registry():
    modules = []
    reuse = {"pts.capsule_area": 0, "pts.capacity_rebuilt": 1, "pts.capacity_simplified": 1,
             "uptake.pts_request": 3, "signal.pts_accepted": 5}
    for old in pts_registry()._modules.values():
        if old.manifest["id"] not in reuse:
            continue
        module = SpatialModule.__new__(SpatialModule)
        module.manifest = deepcopy(old.manifest)
        module.declaration = deepcopy(old.declaration)
        module.manifest["phase"] = reuse[module.manifest["id"]]
        module.declaration["mathematics"].update(
            implementation="friskoli_cad.engine.spatial_modules.SpatialModule",
            assumptions=["spatial-unbiased-v1; fixed known capsule sizes; sampled local substrate.",
                         "PTS signal is observed, not connected to unbiased motion.",
                         "Source equations and limitations: docs/science/pts-minimal.md."])
        modules.append(module)
    xyz = lambda prefix: {f"{prefix}_{a}_um": number("um") for a in "xyz"}
    modules.extend([
        SpatialModule("space.axis_aligned_obstacle", "实体障碍物 / Solid box obstacle", 0, "environment", {},
            {"volume": port("global.scalar", "volume", "um^3")}, {**xyz("lower"), **xyz("upper")}, {},
            r"\Omega_s=[x_0,x_1]\times[y_0,y_1]\times[z_0,z_1]",
            {"\\Omega_s": "closed voxel-aligned solid box [um]"},
            "A physical solid for capsule collision and impermeable field faces; bounds must align with the grid."),
        SpatialModule("source.finite_local", "有限局部来源 / Finite local source", 0, "environment", {},
            {"inventory": SOURCE}, {"species": SPECIES, **xyz("center"), "radius_um": number("um"),
             "initial_molecules": number("molecule"), "release_rate": number("molecule/s")},
            {"inventory": ("global.scalar", "molecule")}, r"a=\min(q\Delta t,N_s),\quad N_s'=N_s-a",
            {"q": "parameters.release_rate [molecule/s]", "N_s": "outputs.inventory [molecule]"},
            "Finite soluble material release. Exhaustion releases zero; no surface-enzyme or polymer-degradation claim."),
        SpatialModule("material.degradable_box", "可降解实体底物 / Degradable material", 0, "environment", {},
            {"inventory": SOURCE}, {"species": SPECIES, **xyz("lower"), **xyz("upper"),
             "initial_molecules": number("molecule")}, {"inventory": ("global.scalar", "molecule")},
            r"N_m'=N_m-\sum_i a_{im}",
            {"N_m": "outputs.inventory [molecule]", "a_{im}": "contact-enzyme accepted soluble release [molecule]"},
            "Inventory counts releasable soluble nutrient equivalents (molecule), not polymer chains. Explicit capsule-contact enzyme releases those equivalents. Partial depletion keeps the box as a geometry approximation; exhaustion removes its collision and diffusion mask after the step, exposing zero-concentration fluid voxels next step. No specific enzyme/polymer/PTS chemistry is claimed."),
        SpatialModule("reaction.contact_degradation", "接触酶降解 / Contact enzyme degradation", 0, "environment", {},
            {"released_amount": port("global.scalar", "released_amount", "molecule")},
            {"kcat_s": number("1/s"), "contact_range_um": number("um")}, {},
            r"a_{im}\le k_{cat}E_i\Delta t/n_i,\quad gap(i,m)\le r_c",
            {"E_i": "surface.enzyme_activity output [molecule]", "n_i": "number of contacted nonempty materials",
             "k_{cat}": "parameters.kcat_s [1/s]", "r_c": "parameters.contact_range_um [um]"},
            "Registered global provider: reads material stocks and surface enzyme copies at step start, shares each cell enzyme budget across contacts, and releases soluble nutrient equivalents before diffusion and uptake. kcat_s is constructed nutrient equivalents per enzyme per second; specific hydrolysis and enzyme kinetics remain replaceable, not experimentally calibrated."),
        SpatialModule("surface.enzyme_activity", "表面酶活性 / Surface enzyme activity", 0, "population", {},
            {"enzyme_copies": port("cell.scalar", "enzyme_copies", "molecule")},
            {"enzyme_copies": number("molecule")}, {}, r"E_i=E_0",
            {"E_i": "outputs.enzyme_copies [molecule]", "E_0": "parameters.enzyme_copies [molecule]"},
            "Explicit constructed surface enzyme copy count. Populations without this module do not degrade material; no synthesis or enzyme turnover model."),
        SpatialModule("field.diffusive_local", "局部扩散场 / Local diffusion field", 1, "environment",
            {}, {"concentration": FIELD},
            {"species": SPECIES, "diffusivity_um2_s": number("um^2/s")},
            {"concentration": ("field.scalar", "uM")}, r"\partial_t C=D\nabla^2 C+Q-U",
            {"C": "outputs.concentration [uM]", "D": "parameters.diffusivity_um2_s [um^2/s]"},
            "Reads all registered source/material inventories of its species globally at step start; no individual source edge. Contact release and finite spontaneous sources feed one no-flux field before stable diffusion and shared accepted uptake."),
        SpatialModule("field.sample_local", "局部浓度与梯度 / Local concentration and gradient", 2, "population",
            {"field": FIELD, "position": POSITION},
            {"concentration": port("cell.scalar", "concentration", "uM", True),
             "gradient": port("cell.vector", "concentration_gradient", "uM/um", True)},
            {"species": SPECIES}, {}, r"C_i=C_{g(x_i)},\quad \boldsymbol{g}_i=\nabla_h C_{g(x_i)}",
            {"C_i": "outputs.concentration [uM]", "\\boldsymbol{g}_i": "outputs.gradient [uM/um]"},
            "Nearest-voxel sampling at step start; gradients respect blocked faces. Grid convergence remains scenario-specific."),
        SpatialModule("uptake.local_settlement", "局部共享摄取 / Local shared uptake", 4, "population",
            {"field": FIELD, "requested_flux": REQUEST},
            {"accepted_flux": ACCEPTED, "accepted_amount": port("cell.scalar", "accepted_amount", "molecule", True),
             "cumulative_uptake": port("cell.scalar", "cumulative_uptake", "molecule", True)},
            {"species": SPECIES, "initial_molecules": number("molecule")},
            {"cumulative_uptake": ("cell.scalar", "molecule")},
            r"a_i\le J_i\Delta t,\quad J_i^{acc}=a_i/\Delta t,\quad I_i'=I_i+a_i",
            {"a_i": "outputs.accepted_amount [molecule]", "I_i": "outputs.cumulative_uptake [molecule]"},
            "All populations sharing a voxel settle together using step-start support. Only explicit uptake nodes consume a species."),
        SpatialModule("motion.unbiased_run_tumble", "无偏随机游走 / Unbiased run and tumble", 6, "population", {},
            {"position": POSITION, "heading": port("cell.vector", "heading", "1"),
             "blocked": port("cell.scalar", "motion_blocked", "1"),
             "turns": port("cell.scalar", "turn_count", "1")},
            {"speed_um_s": number("um/s"), "tumble_rate_s": number("1/s")},
            {"heading": ("cell.vector", "1"), "remaining_wait": ("cell.scalar", "s")},
            r"\dot{x}=v u,\quad T\sim\mathrm{Exp}(\lambda),\quad u\sim\mathrm{Uniform}(S^{d-1})",
            {"v": "parameters.speed_um_s [um/s]", "\\lambda": "parameters.tumble_rate_s [1/s]"},
            "Persistent isotropic walk with saved event clocks and per-cell RNG. Collision may block a segment; no chemotactic bias.")])
    for module in modules:
        key = module.manifest['id']
        if key == 'reaction.contact_degradation':
            module.provides_roles = ['material.degradation']
            module.propose_degradation = contact_degradation_adapter
            module.default_parameters = {'kcat_s': 2, 'contact_range_um': .1}
        elif key == 'surface.enzyme_activity':
            module.default_parameters = {'enzyme_copies': 10}
        if key not in ('space.axis_aligned_obstacle', 'source.finite_local', 'material.degradable_box'):
            continue
        obstacle = key == 'space.axis_aligned_obstacle'
        material = key == 'material.degradable_box'
        module.object_types = [{
            'id': 'space.obstacle_box' if obstacle else 'material.degradable_box' if material else 'source.attractant',
            'kind': 'obstacle_box' if obstacle else 'degradable_box' if material else 'local_source',
            'label': '实体障碍物 / Solid obstacle' if obstacle else '实体底物 / Degradable material' if material else '引诱物源 / Attractant source',
            'description': module.manifest['description'],
            'icon': 'wall' if obstacle else 'source',
            'initializer': {'adapter': 'environment.node@1', 'module': key + '@1.0.0',
                            'data_modules': [] if obstacle else ['field.diffusive_local@1.0.0'],
                            **({'requirements': [{'role': 'material.degradation',
                                'default_module': 'reaction.contact_degradation@1.0.0', 'scope': 'environment'}]} if material else {})},
            'properties': [{'path': name, 'label': '可释放养分当量' if material and name == 'initial_molecules' else name, 'group': 'bounds' if obstacle or material else 'source',
                            'type': schema['type'], 'unit': schema.get('unit', '1'),
                            **{k: schema[k] for k in ('minimum', 'maximum') if k in schema}}
                           for name, schema in module.manifest['parameters'].items()]}]
    return ModuleRegistry(modules, execution_semantics=SPATIAL_PROFILE)
