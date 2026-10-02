"""Explicit execution profiles; legacy graphs retain their original scheduler."""
from friskoli_cad.protocol import ProtocolError

LEGACY_PROFILE = "legacy-explicit-v1"
PTS_PROFILE = "conservative-pts-bulk-v1"
SPATIAL_PROFILE = "spatial-unbiased-v1"
CHEMOTAXIS_PROFILE = "chemotaxis-spatial-v1"


def profile_for_project(project):
    return project.get("execution_profile", LEGACY_PROFILE)


def registry_for_profile(profile=LEGACY_PROFILE):
    if profile == LEGACY_PROFILE:
        from .modules import default_registry
        return default_registry()
    if profile == PTS_PROFILE:
        from .pts_modules import pts_registry
        return pts_registry()
    if profile == SPATIAL_PROFILE:
        from .spatial_modules import spatial_registry
        return spatial_registry()
    if profile == CHEMOTAXIS_PROFILE:
        from .chemotaxis_modules import chemotaxis_registry
        return chemotaxis_registry()
    raise ProtocolError("project.execution_profile", "/execution_profile", "Unsupported execution profile")


def registry_for_project(project):
    return registry_for_profile(profile_for_project(project))


def task_version(profile):
    return {LEGACY_PROFILE: "0.1.0", PTS_PROFILE: "0.2.0", SPATIAL_PROFILE: "0.3.0", CHEMOTAXIS_PROFILE: "0.4.0"}[profile]


def chemotaxis_schedule(plan, registry):
    """Freeze the same signal/motor/physiology boundaries used by execution."""
    schedule = spatial_schedule(plan, registry)
    schedule.update(schedule_version="0.3.0",
        signal_nodes=[n.id for n in plan.nodes if n.module_id.startswith('signal.')],
        motion_nodes=[n.id for n in plan.nodes if n.module_id == 'motion.hazard_run_tumble'],
        physiology_nodes=[n.id for n in plan.nodes if n.module_id.startswith(('growth.', 'metabolism.', 'expression.', 'life.', 'division.'))],
        motor_read_boundary='committed_step_start', observation_interval='every_committed_step')
    schedule['settlements'] = [{"inventory_node": n.id, "participant_nodes": [p.id for p in plan.nodes
        if p.module_id == 'uptake.local_settlement' and p.inputs['field'].source_node == n.id]}
        for n in plan.nodes if n.module_id in ('field.diffusive_local', 'field.ideal_local_reservoir')]
    schedule['degradation_participants']['enzyme_nodes'] = [n.id for n in plan.nodes
        if n.module_id in ('surface.enzyme_activity', 'expression.surface_copies')]
    schedule['field_order'].append('reservoir_replenishment')
    return schedule


def spatial_schedule(plan, registry):
    """Explicit operator splitting, independent of output-frame frequency."""
    return {"schedule_version": "0.2.0", "read_boundary": "step_start", "commit": "atomic",
        "prepare_nodes": [n.id for n in plan.nodes if n.phase < 4],
        "settlements": [{"inventory_node": n.id, "participant_nodes": [p.id for p in plan.nodes
                         if p.module_id == "uptake.local_settlement" and p.inputs["field"].source_node == n.id]}
                        for n in plan.nodes if n.module_id == "field.diffusive_local"],
        "signal_nodes": [n.id for n in plan.nodes if n.module_id == "signal.pts_accepted"],
        "motion_nodes": [n.id for n in plan.nodes if n.module_id == "motion.unbiased_run_tumble"],
        "degradation_nodes": [n.id for n in plan.nodes
            if "material.degradation" in getattr(registry.get(n.module_id, n.module_version), "provides_roles", ())
            and callable(getattr(registry.get(n.module_id, n.module_version), "propose_degradation", None))],
        "field_sources": [{"field_node": n.id, "species": n.parameters["species"].value,
            "source_nodes": [p.id for p in plan.nodes if p.module_id == "source.finite_local"
                             and p.parameters["species"].value == n.parameters["species"].value],
            "material_nodes": [p.id for p in plan.nodes if p.module_id == "material.degradable_box"
                               and p.parameters["species"].value == n.parameters["species"].value]}
            for n in plan.nodes if n.module_id == "field.diffusive_local"],
        "degradation_participants": {
            "material_nodes": [n.id for n in plan.nodes if n.module_id == "material.degradable_box"],
            "enzyme_nodes": [n.id for n in plan.nodes if n.module_id == "surface.enzyme_activity"]},
        "field_order": ["contact_degradation", "finite_release", "stable_diffusion_substeps", "shared_uptake"],
        "motion_policy": "synchronous-event-intervals-conservative-block"}


def pts_schedule(plan):
    """Serializable schedule used by both execution and frozen provenance."""
    bulk = [n for n in plan.nodes if n.module_id == "bulk.finite_uniform"]
    settlements = [n for n in plan.nodes if n.module_id == "uptake.bulk_settlement"]
    return {"schedule_version": "0.1.0", "read_boundary": "step_start", "commit": "atomic",
        "prepare_nodes": [n.id for n in plan.nodes if n.phase < 3],
        "settlements": [{"inventory_node": owner.id, "participant_nodes": [n.id for n in settlements
                         if n.inputs["inventory"].source_node == owner.id]} for owner in bulk],
        "signal_nodes": [n.id for n in plan.nodes if n.module_id == "signal.pts_accepted"]}
