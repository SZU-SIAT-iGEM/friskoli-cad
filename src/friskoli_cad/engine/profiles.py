"""The first release has one explicit execution contract."""
from friskoli_cad.protocol import ProtocolError

MODULAR_PROFILE = "modular-spatial-v1"


def profile_for_project(project):
    if project.get("project_version") != "0.6.0":
        raise ProtocolError("project.version", "/project_version", "Only Project 0.6.0 is supported; historical projects are not migrated.")
    if project.get("execution_profile") != MODULAR_PROFILE:
        raise ProtocolError("project.execution_profile", "/execution_profile", "Only modular-spatial-v1 is supported.")
    return MODULAR_PROFILE


def registry_for_profile(profile=MODULAR_PROFILE):
    if profile != MODULAR_PROFILE:
        raise ProtocolError("project.execution_profile", "/execution_profile", "Only modular-spatial-v1 is supported.")
    from .science_extensions import modular_registry
    return modular_registry()


def registry_for_project(project):
    registry = registry_for_profile(profile_for_project(project))
    if project.get("dependency_lock") is not None:
        from friskoli_cad.packages import PackageStore
        registry = PackageStore.default().extend_registry(registry, project["dependency_lock"])
    return registry


def task_version(profile=MODULAR_PROFILE):
    if profile != MODULAR_PROFILE:
        raise ProtocolError("project.execution_profile", "/execution_profile", "Only modular-spatial-v1 is supported.")
    return "0.6.0"
