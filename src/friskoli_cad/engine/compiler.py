"""Compile a validated behavior graph into a deterministic execution plan."""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from types import MappingProxyType
from typing import Iterable, Mapping

from friskoli_cad.protocol import validate_graph


@dataclass(frozen=True, slots=True)
class ParameterValue:
    value: object
    unit: str | None
    provenance_kind: str
    provenance_reference: str


@dataclass(frozen=True, slots=True)
class InputBinding:
    source_node: str
    source_port: str
    timing: str


@dataclass(frozen=True, slots=True)
class CompiledNode:
    id: str
    module_id: str
    module_version: str
    owner_kind: str
    owner_id: str
    phase: int
    parameters: Mapping[str, ParameterValue]
    inputs: Mapping[str, InputBinding]
    outputs: tuple[str, ...]
    initial_outputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledGraph:
    id: str
    nodes: tuple[CompiledNode, ...]
    by_id: Mapping[str, CompiledNode]


def compile_graph(
    graph: Mapping[str, object], manifests: Iterable[Mapping[str, object]]
) -> CompiledGraph:
    """Return an immutable plan ordered by phase, dependency, then node ID.

    ``previous_step`` bindings are recorded but do not constrain the current
    step's order. Their source output must exist at t=0, as checked by the
    protocol validator.
    """
    from .module_api import freeze
    declarations = tuple(manifests)
    validate_graph(graph, declarations)
    registry = {(item["id"], item["version"]): item for item in declarations}
    nodes = {node["id"]: node for node in graph["nodes"]}
    phases = {
        node_id: registry[(node["module_id"], node["module_version"])]["phase"]
        for node_id, node in nodes.items()
    }
    bindings: dict[str, dict[str, InputBinding]] = {node_id: {} for node_id in nodes}
    successors: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    indegree = {node_id: 0 for node_id in nodes}

    for edge in graph["edges"]:
        source = edge["from"]["node"]
        target = edge["to"]["node"]
        bindings[target][edge["to"]["port"]] = InputBinding(
            source_node=source,
            source_port=edge["from"]["port"],
            timing=edge["timing"],
        )
        if edge["timing"] == "same_step" and target not in successors[source]:
            successors[source].add(target)
            indegree[target] += 1

    ready: list[tuple[int, str]] = []
    for node_id, degree in indegree.items():
        if degree == 0:
            heappush(ready, (phases[node_id], node_id))
    order = []
    while ready:
        _, node_id = heappop(ready)
        order.append(node_id)
        for target in sorted(successors[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                heappush(ready, (phases[target], target))
    if len(order) != len(nodes):
        raise RuntimeError("protocol validation accepted a cyclic same_step graph")

    compiled = []
    for node_id in order:
        node = nodes[node_id]
        manifest = registry[(node["module_id"], node["module_version"])]
        parameters = {
            name: ParameterValue(
                value=freeze(entry["value"]),
                unit=entry.get("unit"),
                provenance_kind=entry["provenance"]["kind"],
                provenance_reference=entry["provenance"]["reference"],
            )
            for name, entry in node["parameters"].items()
        }
        compiled.append(CompiledNode(
            id=node_id,
            module_id=node["module_id"],
            module_version=node["module_version"],
            owner_kind=node["owner"]["kind"],
            owner_id=node["owner"]["id"],
            phase=manifest["phase"],
            parameters=MappingProxyType(parameters),
            inputs=MappingProxyType(bindings[node_id].copy()),
            outputs=tuple(manifest["outputs"]),
            initial_outputs=tuple(manifest["initial_outputs"]),
        ))
    compiled_nodes = tuple(compiled)
    return CompiledGraph(
        id=graph["id"],
        nodes=compiled_nodes,
        by_id=MappingProxyType({node.id: node for node in compiled_nodes}),
    )
