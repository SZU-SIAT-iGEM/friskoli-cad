"""Structural and cross-reference checks for protocol version 0.1.0.

These checks are shared by importers and the future graph compiler. They do not
execute a model or claim scientific validity for any module.
"""

from __future__ import annotations

import json
import math
from collections import deque
from functools import lru_cache
from importlib.resources import files
from typing import Iterable, Mapping

from jsonschema import Draft202012Validator


class ProtocolError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}: {message}")


@lru_cache(maxsize=4)
def _validator(name: str) -> Draft202012Validator:
    resource = files("friskoli_cad.protocol").joinpath("schemas", f"{name}.schema.json")
    schema = json.loads(resource.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _pointer(parts: Iterable[object]) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _check_finite(value: object, path: str = "") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ProtocolError("number.non_finite", path or "/", "numbers must be finite")
    if isinstance(value, dict):
        for key, child in value.items():
            _check_finite(child, path + "/" + str(key).replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_finite(child, path + f"/{index}")


def _check_schema(name: str, document: object) -> None:
    _check_finite(document)
    error = next(_validator(name).iter_errors(document), None)
    if error is not None:
        raise ProtocolError("schema.invalid", _pointer(error.absolute_path), error.message)


def _fail(code: str, path: str, message: str) -> None:
    raise ProtocolError(code, path, message)


def validate_manifest(manifest: Mapping[str, object]) -> None:
    """Check a module declaration, including semantic references inside it."""
    _check_schema("module", manifest)
    parameters = manifest["parameters"]
    outputs = manifest["outputs"]
    for name, definition in parameters.items():
        minimum = definition.get("minimum")
        maximum = definition.get("maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            _fail("parameter.range", f"/parameters/{name}", "minimum exceeds maximum")
    for side in ("inputs", "outputs"):
        for name, port in manifest[side].items():
            path = f"/{side}/{name}"
            if side == "outputs" and "optional" in port:
                _fail("port.optional_output", path, "optional applies only to inputs")
            binding = port.get("species_parameter")
            if binding is not None and (
                binding not in parameters or parameters[binding]["type"] != "string"
            ):
                _fail("port.species_binding", path, "species_parameter must name a string parameter")
    for name, state in manifest["state"].items():
        is_cell = state["shape"].startswith("cell.")
        rule = state["on_division"]
        if (is_cell and rule == "not_applicable") or (not is_cell and rule != "not_applicable"):
            _fail("state.division_rule", f"/state/{name}", "division rule does not match state shape")
    unknown = set(manifest["initial_outputs"]) - set(outputs)
    if unknown:
        _fail("port.initial_output", "/initial_outputs", f"unknown output: {sorted(unknown)[0]}")


def _parameter_value(node: Mapping[str, object], index: int, name: str, definition: Mapping[str, object]) -> None:
    entry = node["parameters"][name]
    value = entry["value"]
    declared = definition["type"]
    actual = type(value)
    valid = {
        "number": actual in (int, float),
        "integer": actual is int,
        "boolean": actual is bool,
        "string": actual is str,
    }[declared]
    path = f"/nodes/{index}/parameters/{name}"
    if not valid:
        _fail("parameter.type", path, f"expected {declared}")
    if declared in ("number", "integer"):
        if entry.get("unit") != definition["unit"]:
            _fail("parameter.unit", path, f"expected unit {definition['unit']}")
        if "minimum" in definition and value < definition["minimum"]:
            _fail("parameter.range", path, "value is below minimum")
        if "maximum" in definition and value > definition["maximum"]:
            _fail("parameter.range", path, "value is above maximum")
    elif "unit" in entry:
        _fail("parameter.unit", path, "non-numeric parameter has a unit")


def _species(node: Mapping[str, object], port: Mapping[str, object]) -> str | None:
    binding = port.get("species_parameter")
    return node["parameters"][binding]["value"] if binding is not None else None


def validate_graph(graph: Mapping[str, object], manifests: Iterable[Mapping[str, object]]) -> None:
    """Check a graph against exact module versions and typed port contracts.

    Unit matching is deliberately exact. A conversion requires an explicit
    conversion module so the graph records it.
    """
    _check_schema("graph", graph)
    registry = {}
    for manifest in manifests:
        validate_manifest(manifest)
        key = (manifest["id"], manifest["version"])
        if key in registry:
            _fail("module.duplicate", "/manifests", f"duplicate module {key}")
        registry[key] = manifest

    nodes = {}
    node_indices = {}
    for index, node in enumerate(graph["nodes"]):
        node_id = node["id"]
        if node_id in nodes:
            _fail("node.duplicate", f"/nodes/{index}/id", f"duplicate node {node_id}")
        key = (node["module_id"], node["module_version"])
        if key not in registry:
            _fail("module.unknown", f"/nodes/{index}", f"module {key} is not registered")
        manifest = registry[key]
        if node["owner"]["kind"] != manifest["scope"]:
            _fail("node.scope", f"/nodes/{index}/owner", "owner kind differs from module scope")
        supplied = set(node["parameters"])
        declared = set(manifest["parameters"])
        if supplied != declared:
            _fail(
                "parameter.set", f"/nodes/{index}/parameters",
                f"missing {sorted(declared - supplied)}, unknown {sorted(supplied - declared)}",
            )
        for name, definition in manifest["parameters"].items():
            _parameter_value(node, index, name, definition)
        nodes[node_id] = (node, manifest)
        node_indices[node_id] = index

    edge_ids = set()
    incoming = set()
    adjacency = {node_id: set() for node_id in nodes}
    for index, edge in enumerate(graph["edges"]):
        path = f"/edges/{index}"
        if edge["id"] in edge_ids:
            _fail("edge.duplicate", path + "/id", "duplicate edge ID")
        edge_ids.add(edge["id"])
        src_id, dst_id = edge["from"]["node"], edge["to"]["node"]
        if src_id not in nodes or dst_id not in nodes:
            _fail("edge.node", path, "edge refers to an unknown node")
        src, src_manifest = nodes[src_id]
        dst, dst_manifest = nodes[dst_id]
        out_name, in_name = edge["from"]["port"], edge["to"]["port"]
        if out_name not in src_manifest["outputs"] or in_name not in dst_manifest["inputs"]:
            _fail("edge.port", path, "edge refers to an unknown output or input port")
        output = src_manifest["outputs"][out_name]
        input_ = dst_manifest["inputs"][in_name]
        output_type = (output["shape"], output["quantity"], output["unit"])
        input_type = (input_["shape"], input_["quantity"], input_["unit"])
        if output_type != input_type:
            _fail("edge.type", path, "port shape, quantity or unit differs")
        if _species(src, output) != _species(dst, input_):
            _fail("edge.species", path, "port species differs or is undeclared")
        if output["shape"].startswith("cell.") and src["owner"] != dst["owner"]:
            _fail("edge.population", path, "cell ports must belong to the same population")
        target = (dst_id, in_name)
        if target in incoming:
            _fail("edge.multiple_inputs", path, "input has more than one provider")
        incoming.add(target)
        if edge["timing"] == "same_step":
            if src_manifest["phase"] > dst_manifest["phase"]:
                _fail("edge.phase", path, "backward connection requires previous_step")
            adjacency[src_id].add(dst_id)
        elif out_name not in src_manifest["initial_outputs"]:
            _fail("edge.initial", path, "previous_step source has no initial output")

    for node_id, (_, manifest) in nodes.items():
        for name, port in manifest["inputs"].items():
            if not port.get("optional", False) and (node_id, name) not in incoming:
                _fail(
                    "edge.required", f"/nodes/{node_indices[node_id]}/inputs/{name}",
                    "required input has no provider",
                )

    indegree = {node_id: 0 for node_id in nodes}
    for successors in adjacency.values():
        for successor in successors:
            indegree[successor] += 1
    ready = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        node_id = ready.popleft()
        visited += 1
        for successor in adjacency[node_id]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
    if visited != len(nodes):
        _fail("edge.cycle", "/edges", "same_step connections contain a cycle")


def validate_run_metadata(
    run: Mapping[str, object], graph: Mapping[str, object], manifests: Iterable[Mapping[str, object]]
) -> None:
    """Check observable channels against the validated graph's cell outputs."""
    _check_schema("run", run)
    manifests = list(manifests)
    validate_graph(graph, manifests)
    if run["graph_id"] != graph["id"]:
        _fail("run.graph", "/graph_id", "run metadata names another graph")
    registry = {(manifest["id"], manifest["version"]): manifest for manifest in manifests}
    nodes = {node["id"]: node for node in graph["nodes"]}
    groups = {node["owner"]["id"] for node in graph["nodes"] if node["owner"]["kind"] == "population"}
    if set(run["groups"]) != groups:
        _fail("run.groups", "/groups", "groups differ from graph population owners")
    for channel_id, channel in run["channels"].items():
        path = f"/channels/{channel_id}"
        node = nodes.get(channel["node"])
        if node is None or node["owner"]["kind"] != "population":
            _fail("channel.node", path, "channel must refer to a population node")
        if channel["group_id"] != node["owner"]["id"]:
            _fail("channel.group", path, "channel group differs from its node owner")
        manifest = registry[(node["module_id"], node["module_version"])]
        port = manifest["outputs"].get(channel["port"])
        if port is None or port["shape"] not in ("cell.scalar", "cell.index"):
            _fail("channel.port", path, "channel must refer to a numeric per-cell output")
        if (channel["shape"], channel["quantity"], channel["unit"]) != (port["shape"], port["quantity"], port["unit"]):
            _fail("channel.type", path, "channel shape, quantity or unit differs from the module output")


class FrameSequenceValidator:
    """Incrementally check complete cell snapshots and intervening events."""

    def __init__(self, run: Mapping[str, object] | None = None) -> None:
        if run is not None:
            _check_schema("run", run)
        self.run = run
        self.run_id: str | None = None
        self.next_index = 0
        self.previous_time: float | None = None
        self.alive: dict[str, str] = {}
        self.seen: set[str] = set()

    def accept(self, frame: Mapping[str, object]) -> None:
        _check_schema("frame", frame)
        index = frame["frame_index"]
        if index != self.next_index:
            _fail("frame.order", "/frame_index", f"expected {self.next_index}")
        if self.run_id is not None and frame["run_id"] != self.run_id:
            _fail("frame.run", "/run_id", "run ID changed within a sequence")
        if self.run is not None and frame["run_id"] != self.run["run_id"]:
            _fail("frame.run", "/run_id", "run ID differs from run metadata")
        time = frame["time_s"]
        if self.previous_time is None:
            if time != 0 or frame["events"]:
                _fail("frame.initial", "/", "frame 0 starts at t=0 without events")
        elif time <= self.previous_time:
            _fail("frame.time", "/time_s", "frame time must increase")

        alive = self.alive.copy()
        seen = self.seen.copy()
        event_time = self.previous_time if self.previous_time is not None else 0
        for event_index, event in enumerate(frame["events"]):
            path = f"/events/{event_index}"
            if not event_time <= event["time_s"] <= time:
                _fail("event.time", path, "events must be ordered within the frame interval")
            event_time = event["time_s"]
            kind = event["type"]
            if kind == "birth":
                cell_id = event["cell_id"]
                if cell_id in seen:
                    _fail("cell.reused", path, "cell ID has already appeared")
                if self.run is not None and event["group_id"] not in self.run["groups"]:
                    _fail("cell.group", path, "birth group is absent from run metadata")
                alive[cell_id] = event["group_id"]
                seen.add(cell_id)
            elif kind == "division":
                parent, child = event["parent_id"], event["child_id"]
                if parent not in alive or child in seen:
                    _fail("cell.division", path, "division needs a living parent and a new child ID")
                alive[child] = alive[parent]
                seen.add(child)
            else:
                cell_id = event["cell_id"]
                if cell_id not in alive:
                    _fail("cell.death", path, "death needs a living cell")
                del alive[cell_id]

        observed = {}
        for cell_index, cell in enumerate(frame["cells"]):
            cell_id = cell["id"]
            if cell_id in observed:
                _fail("cell.duplicate", f"/cells/{cell_index}/id", "cell appears twice in a frame")
            observed[cell_id] = cell["group_id"]
            if self.run is not None:
                if cell["group_id"] not in self.run["groups"]:
                    _fail("cell.group", f"/cells/{cell_index}/group_id", "group is absent from run metadata")
                for channel_id, value in cell["channels"].items():
                    channel_path = f"/cells/{cell_index}/channels/{channel_id}"
                    channel = self.run["channels"].get(channel_id)
                    if channel is None:
                        _fail("cell.channel", channel_path, "channel is absent from run metadata")
                    if channel["group_id"] != cell["group_id"]:
                        _fail("cell.channel", channel_path, "channel belongs to another population")
                    if channel["shape"] == "cell.index" and type(value) is not int:
                        _fail("cell.channel", channel_path, "index channel must be an integer")
            quaternion = cell["orientation_xyzw"]
            norm = sum(component * component for component in quaternion)
            if not math.isclose(norm, 1.0, rel_tol=0, abs_tol=1e-3):
                _fail("cell.orientation", f"/cells/{cell_index}/orientation_xyzw", "quaternion must have unit length")
        if self.previous_time is None:
            alive = observed.copy()
        elif observed != alive:
            _fail("frame.cells", "/cells", "cell snapshot does not match the event history")

        self.run_id = frame["run_id"]
        self.next_index += 1
        self.previous_time = time
        self.alive = alive
        self.seen = seen | set(observed)


def validate_frame_sequence(
    frames: Iterable[Mapping[str, object]], run: Mapping[str, object] | None = None
) -> None:
    validator = FrameSequenceValidator(run)
    for frame in frames:
        validator.accept(frame)
    if validator.next_index == 0:
        _fail("frame.empty", "/", "frame sequence is empty")
