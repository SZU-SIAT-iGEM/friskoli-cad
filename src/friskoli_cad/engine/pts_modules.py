"""Typed declarations for the deliberately small conservative PTS profile."""
from copy import deepcopy

from .profiles import PTS_PROFILE
from .runtime import ModuleRegistry, SimulationError


def port(shape, quantity, unit, species=False):
    result = {"shape": shape, "quantity": quantity, "unit": unit}
    if species:
        result["species_parameter"] = "species"
    return result


def number(unit="1", minimum=0, maximum=None):
    result = {"type": "number", "unit": unit, "minimum": minimum}
    if maximum is not None:
        result["maximum"] = maximum
    return result


SPECIES = {"type": "string"}
AREA = port("cell.scalar", "surface_area", "um^2")
COPIES = port("cell.scalar", "carrier_copies", "molecule")
CONCENTRATION = port("cell.scalar", "concentration", "uM", True)
REQUEST = port("cell.scalar", "requested_flux", "molecule/s", True)
ACCEPTED = port("cell.scalar", "accepted_flux", "molecule/s", True)
INVENTORY = port("global.scalar", "bulk_inventory", "molecule", True)
BULK_C = port("global.scalar", "concentration", "uM", True)
SIGNAL_PARAMETERS = {
    "ei_total_um": number("uM"), "ei_dephos_per_molecule": number("1/molecule"),
    "ei_rephos_s": number("1/s"), "ei_chea_inhibition_um": number("uM"),
    "chea_total_um": number("uM"), "chey_total_um": number("uM"),
    "chey_phos_per_um_s": number("1/(uM*s)"), "chey_dephos_s": number("1/s"),
    "motor_hill": number(), "motor_half_um": number("uM"),
}


class PTSModule:
    world_access = "transactional"

    def __init__(self, module_id, label, phase, scope, inputs, outputs, parameters,
                 states, latex, symbols, description):
        self.manifest = {"protocol_version": "0.1.0", "id": module_id, "version": "1.0.0",
            "scope": scope, "phase": phase, "scientific_role": "mechanism",
            "maturity": "exploratory", "description": description,
            "inputs": deepcopy(inputs), "outputs": deepcopy(outputs),
            "parameters": deepcopy(parameters), "state": {
                name: {"shape": shape, "unit": unit,
                       "on_division": "copy" if shape.startswith("cell.") else "not_applicable"}
                for name, (shape, unit) in states.items()}, "initial_outputs": list(outputs)}
        self.declaration = {"label": label, "category": "mechanism", "mathematics": {
            "kind": "equations", "equations": [{"latex": latex, "symbols": symbols}],
            "algorithm": description,
            "implementation": "friskoli_cad.engine.pts_modules.PTSModule",
            "verification": {"status": "tested", "tests": ["tests/test_pts_runtime.py"]},
            "assumptions": ["conservative-pts-bulk-v1 only; static known capsules; well-mixed finite bulk.",
                "No growth, division, death, diffusion or movement; parameters are not experimentally calibrated.",
                "Equations and source-specific limitations: docs/science/pts-minimal.md."]}}

    def initialize(self, *args):
        raise SimulationError("profile.required", "PTS mechanisms require their transactional execution profile")

    advance = initialize


def pts_registry():
    modules = [
        PTSModule("bulk.finite_uniform", "有限均匀底物 / Finite uniform bulk", 0, "environment", {},
            {"inventory": INVENTORY, "concentration": BULK_C},
            {"species": SPECIES, "allocation_policy": {"type": "string"}},
            {"inventory": ("global.scalar", "molecule")},
            r"N=C V N_A 10^{-21},\quad N^{n+1}=N^n-\sum_i a_i",
            {"N": "outputs.inventory [molecule]", "C": "outputs.concentration [uM]", "V": "domain volume [um^3]", "a_i": "accepted amount over this step [molecule]"},
            "One inventory owner per species. Initial concentration comes from project species. All groups share the same well-mixed reservoir; strict or explicit proportional allocation."),
        PTSModule("pts.capsule_area", "菌体表面积 / Capsule surface area", 0, "population", {},
            {"surface_area": AREA}, {}, {}, r"A=\pi d L",
            {"A": "outputs.surface_area [um^2]", "L": "pole-to-pole capsule length [um]", "d": "capsule diameter [um]"},
            "Read fixed capsule geometry; total length includes both end caps."),
        PTSModule("pts.capacity_rebuilt", "PTS 容量 A / PTS capacity A", 1, "population",
            {"surface_area": AREA}, {"functional_copies": COPIES, "g_effective": port("cell.scalar", "expression_gain", "1")},
            {"g_requested": number(), "reference_pts_copies": number("molecule"), "basal_inner_fraction": number(maximum=1),
             "pts_max_available_fraction": number(maximum=1), "ascf_area_um2": number("um^2/molecule")}, {},
            r"N_{\rm PTS}=\min(gN_{\rm ref},A(1-f_0)f_{\rm PTS}/a_{\rm scf})",
            {"N_{\\rm PTS}": "outputs.functional_copies [molecule]", "A": "inputs.surface_area [um^2]", "g": "parameters.g_requested"},
            "Rebuilt source footprint-limited total copies; source A parameterization remains distinct."),
        PTSModule("pts.capacity_simplified", "PTS 容量 B / PTS capacity B", 1, "population",
            {"surface_area": AREA}, {"functional_copies": COPIES, "g_effective": port("cell.scalar", "expression_gain", "1")},
            {"g_requested": number(), "reference_pts_copies": number("molecule"), "g_cap": number(), "reference_area_um2": number("um^2")}, {},
            r"N_{\rm PTS}=N_{\rm ref}\min(g,g_{\rm cap}A/A_{\rm ref})",
            {"N_{\\rm PTS}": "outputs.functional_copies [molecule]", "A": "inputs.surface_area [um^2]", "g": "parameters.g_requested"},
            "Simplified source reference-area-scaled total copies; this is not the rebuilt footprint rule."),
        PTSModule("bulk.sample_uniform", "均匀底物采样 / Uniform bulk sample", 1, "population",
            {"concentration": BULK_C}, {"concentration": CONCENTRATION}, {"species": SPECIES}, {},
            r"C_i^n=C_{\rm bulk}^n", {"C_i^n": "outputs.concentration [uM]"},
            "Every static cell explicitly samples the whole well-mixed bulk at the step start. No local gradient or spatial contact is implied."),
        PTSModule("uptake.pts_request", "PTS 摄取请求 / PTS uptake request", 2, "population",
            {"concentration": CONCENTRATION, "functional_copies": COPIES}, {"requested_flux": REQUEST},
            {"species": SPECIES, "turnover_s": number("1/s"), "half_saturation_um": number("uM")}, {},
            r"J_i^{\rm req}=k_{\rm cat}N_i\frac{C_i}{K_M+C_i}",
            {"J_i^{\\rm req}": "outputs.requested_flux [molecule/s]", "N_i": "inputs.functional_copies [molecule]", "C_i": "inputs.concentration [uM]"},
            "Potential uptake only; does not debit inventory or imply that requested material is available."),
        PTSModule("uptake.bulk_settlement", "共享库存结算 / Shared bulk settlement", 3, "population",
            {"inventory": INVENTORY, "requested_flux": REQUEST},
            {"accepted_flux": ACCEPTED, "accepted_amount": port("cell.scalar", "accepted_amount", "molecule", True),
             "cumulative_uptake": port("cell.scalar", "intracellular_amount", "molecule", True)},
            {"species": SPECIES, "initial_intracellular_molecules": number("molecule")},
            {"cumulative_uptake": ("cell.scalar", "molecule")},
            r"q_i=J_i^{\rm req}\Delta t,\quad a_i=q_i\min(1,N/\sum_jq_j),\quad U_i^{n+1}=U_i^n+a_i",
            {"q_i": "requested amount [molecule]", "a_i": "outputs.accepted_amount [molecule]", "N": "inputs.inventory [molecule]", "U_i": "outputs.cumulative_uptake [molecule]"},
            "All settlement nodes bound to the same inventory settle together once. Formula shows proportional policy; strict policy rejects a shortage. Cumulative intracellular amount has no consumption in this profile."),
        PTSModule("signal.pts_accepted", "PTS 接受通量信号 / Accepted-flux PTS signal", 4, "population",
            {"accepted_flux": ACCEPTED},
            {"ei_fraction": port("cell.scalar", "ei_dephosphorylated_fraction", "1"),
             "chea_active": port("cell.scalar", "chea_active_concentration", "uM"),
             "chey_p": port("cell.scalar", "chey_phosphorylated_concentration", "uM"),
             "motor_bias": port("cell.scalar", "motor_bias", "1")},
            {"species": SPECIES, **SIGNAL_PARAMETERS, "initial_ei_fraction": number(maximum=1), "initial_chey_p_um": number("uM")},
            {"ei_fraction": ("cell.scalar", "1"), "chey_p": ("cell.scalar", "uM")},
            r"\dot e=k_dJ_{\rm acc}(1-e)-k_re,\quad A=\frac{A_T}{1+E_Te/K_I},\quad\dot Y=k_yA(Y_T-Y)-k_zY,\quad b=\frac{Y^h}{K_M^h+Y^h}",
            {"e": "outputs.ei_fraction; dephosphorylated EI fraction", "J_{\\rm acc}": "inputs.accepted_flux [molecule/s]", "A": "outputs.chea_active [uM]", "Y": "outputs.chey_p [uM]"},
            "Source phenomenological signal. Exact EI frozen-flux step, then CheY exponential step with end-step CheA frozen (first-order coupling); motor bias is a readout, no motor or motion is executed."),
    ]
    return ModuleRegistry(modules, PTS_PROFILE)
