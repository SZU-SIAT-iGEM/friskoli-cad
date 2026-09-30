"""Explicit execution profiles; legacy graphs retain their original scheduler."""
from friskoli_cad.protocol import ProtocolError

LEGACY_PROFILE = "legacy-explicit-v1"
PTS_PROFILE = "conservative-pts-bulk-v1"


def profile_for_project(project):
    return project.get("execution_profile", LEGACY_PROFILE)


def registry_for_profile(profile=LEGACY_PROFILE):
    if profile == LEGACY_PROFILE:
        from .modules import default_registry
        return default_registry()
    if profile == PTS_PROFILE:
        from .pts_modules import pts_registry
        return pts_registry()
    raise ProtocolError("project.execution_profile", "/execution_profile", "Unsupported execution profile")


def registry_for_project(project):
    return registry_for_profile(profile_for_project(project))


def pts_schedule(plan):
    """Serializable schedule used by both execution and frozen provenance."""
    bulk = [n for n in plan.nodes if n.module_id == "bulk.finite_uniform"]
    settlements = [n for n in plan.nodes if n.module_id == "uptake.bulk_settlement"]
    return {"schedule_version": "0.1.0", "read_boundary": "step_start", "commit": "atomic",
        "prepare_nodes": [n.id for n in plan.nodes if n.phase < 3],
        "settlements": [{"inventory_node": owner.id, "participant_nodes": [n.id for n in settlements
                         if n.inputs["inventory"].source_node == owner.id]} for owner in bulk],
        "signal_nodes": [n.id for n in plan.nodes if n.module_id == "signal.pts_accepted"]}
