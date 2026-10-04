"""A single registry of manifests and executable module proposals."""
from copy import deepcopy
from types import MappingProxyType
from typing import Iterable, Mapping
from friskoli_cad.protocol import validate_manifest
from friskoli_cad.registry import build_catalog
from .core import SimulationError

class ModuleRegistry:
    def __init__(self, modules: Iterable[object], execution_semantics="modular-spatial-v1"):
        by_key = {}
        for module in modules:
            validate_manifest(module.manifest)
            key = module.manifest["id"], module.manifest["version"]
            if key in by_key:
                raise SimulationError("module.duplicate", f"module {key} is registered twice")
            by_key[key] = module
        self._modules = MappingProxyType(by_key)
        self._catalog = build_catalog(by_key.values(), execution_semantics)

    @property
    def catalog(self) -> dict:
        return deepcopy(self._catalog)

    @property
    def manifests(self) -> tuple[Mapping[str, object], ...]:
        return tuple(module.manifest for module in self._modules.values())

    def get(self, module_id: str, version: str) -> object:
        try:
            return self._modules[(module_id, version)]
        except KeyError as error:
            raise SimulationError("module.missing", f"module {(module_id, version)} has no runtime") from error
