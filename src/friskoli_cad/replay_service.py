"""Small local HTTP service for protocol-based simulation replay."""

from __future__ import annotations

import argparse
import json
import math
import hashlib
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Mapping

from friskoli_cad.engine import SimulationError
from friskoli_cad.engine.modules import default_registry
from friskoli_cad.project import simulation_from_project
from friskoli_cad.project import validate_project
from friskoli_cad.protocol import ProtocolError, validate_frame_sequence


EXAMPLE_PROJECT = files("friskoli_cad").joinpath("examples", "workspace_3d.project.json")
MAX_REQUEST_BYTES = 1_000_000
MAX_REPLAY_VALUES = 1_000_000
MAX_VIEW_TILES = 4_096
MAX_VIEW_CELLS = 2_000
STATIC_FILES = {
    "/registry.css": ("registry.css", "text/css; charset=utf-8"),
    "/migration.mjs": ("migration.mjs", "text/javascript; charset=utf-8"),
    "/math-inspector.mjs": ("math-inspector.mjs", "text/javascript; charset=utf-8"),
    "/vendor/katex/katex.mjs": ("vendor/katex/katex.mjs", "text/javascript; charset=utf-8"),
    "/vendor/katex/katex.min.css": ("vendor/katex/katex.min.css", "text/css; charset=utf-8"),
    "/panels.mjs": ("panels.mjs", "text/javascript; charset=utf-8"),
    "/results.mjs": ("results.mjs", "text/javascript; charset=utf-8"),
    "/workspace.mjs": ("workspace.mjs", "text/javascript; charset=utf-8"),
    "/kernel-client.mjs": ("kernel-client.mjs", "text/javascript; charset=utf-8"),
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.mjs": ("app.mjs", "text/javascript; charset=utf-8"),
    "/replay.mjs": ("replay.mjs", "text/javascript; charset=utf-8"),
    "/i18n.mjs": ("i18n.mjs", "text/javascript; charset=utf-8"),
    "/scene3d.mjs": ("scene3d.mjs", "text/javascript; charset=utf-8"),
    "/catalog.mjs": ("catalog.mjs", "text/javascript; charset=utf-8"),
    "/population.mjs": ("population.mjs", "text/javascript; charset=utf-8"),
    "/workflow.mjs": ("workflow.mjs", "text/javascript; charset=utf-8"),
    "/graph-edit.mjs": ("graph-edit.mjs", "text/javascript; charset=utf-8"),
    "/placeables.mjs": ("placeables.mjs", "text/javascript; charset=utf-8"),
    "/icons.mjs": ("icons.mjs", "text/javascript; charset=utf-8"),
    "/vendor/three/build/three.module.js": ("vendor/three/build/three.module.js", "text/javascript; charset=utf-8"),
    "/vendor/three/build/three.core.js": ("vendor/three/build/three.core.js", "text/javascript; charset=utf-8"),
    "/vendor/three/examples/jsm/controls/OrbitControls.js": ("vendor/three/examples/jsm/controls/OrbitControls.js", "text/javascript; charset=utf-8"),
    "/vendor/three/examples/jsm/controls/TransformControls.js": ("vendor/three/examples/jsm/controls/TransformControls.js", "text/javascript; charset=utf-8"),
    "/assets/friskoli.svg": ("assets/friskoli.svg", "image/svg+xml"),
    "/assets/cad.svg": ("assets/cad.svg", "image/svg+xml"),
}


for _font in files("friskoli_cad").joinpath("web", "vendor", "katex", "fonts").iterdir():
    _extension = _font.name.rsplit(".", 1)[-1]
    if _font.is_file() and _extension in ("woff2", "woff", "ttf"):
        STATIC_FILES[f"/vendor/katex/fonts/{_font.name}"] = (
            f"vendor/katex/fonts/{_font.name}", f"font/{_extension}")


class ReplayRequestError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.status = status
        super().__init__(message)


def _snapshot_payload(snapshot) -> dict:
    return {
        "frame": snapshot.cell_frame,
        "concentrations": {
            species: {"unit": snapshot.concentration_units[species], "values_zyx": values.tolist()}
            for species, values in snapshot.concentration_fields.items()
        },
    }


def prepare_project(project: Mapping[str, object], *, dt_s: float, steps: int):
    """Validate and limit allocations before creating the initial state; never advances time."""
    if type(steps) is not int or not 1 <= steps <= 100:
        raise ReplayRequestError("replay.steps", "steps must be an integer from 1 to 100")
    if type(dt_s) not in (int, float) or not math.isfinite(dt_s) or dt_s <= 0:
        raise ReplayRequestError("replay.dt", "dt_s must be positive and finite")
    validate_project(project, default_registry().manifests)
    nx, ny, nz = project["domain"]["counts_xyz"]
    if nx * ny > MAX_VIEW_TILES or nx * ny * nz * (steps + 1) > MAX_REPLAY_VALUES:
        raise ReplayRequestError("replay.size", "grid exceeds the local viewer limit", 413)
    if sum(len(group["ids"]) for group in project["groups"].values()) > MAX_VIEW_CELLS:
        raise ReplayRequestError("replay.cell_count", "cell count exceeds the local viewer limit", 413)
    simulation = simulation_from_project(project)
    initial = simulation.current
    field_count = max(1, len(initial.concentration_fields))
    if initial.domain.nx * initial.domain.ny > MAX_VIEW_TILES:
        raise ReplayRequestError("replay.view_size", "XY slice exceeds the local viewer limit", 413)
    if len(initial.cell_frame["cells"]) > MAX_VIEW_CELLS:
        raise ReplayRequestError("replay.cell_count", "cell count exceeds the local viewer limit", 413)
    if initial.domain.voxel_count * field_count * (steps + 1) > MAX_REPLAY_VALUES:
        raise ReplayRequestError("replay.size", "replay field data exceeds the local viewer limit", 413)
    return simulation


def build_replay(project: Mapping[str, object], *, dt_s: float, steps: int) -> dict:
    """Run an immutable project and expose existing frames without changing their schema."""
    project = deepcopy(project)
    simulation = prepare_project(project, dt_s=dt_s, steps=steps)
    initial = simulation.current
    snapshots = [_snapshot_payload(initial)]
    for _ in range(steps):
        snapshots.append(_snapshot_payload(simulation.step(dt_s)))
        if len(snapshots[-1]["frame"]["cells"]) > MAX_VIEW_CELLS:
            raise ReplayRequestError("replay.cell_count", "cell count exceeds the local viewer limit", 413)
    validate_frame_sequence((item["frame"] for item in snapshots), project["run"])
    return {
        "replay_format_version": "0.1.0",
        "project_id": project["id"],
        "run": project["run"],
        "domain": project["domain"],
        "snapshots": snapshots,
    }


class ReplayHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = self.path.split("?", 1)[0]
        if path == "/api/example-project":
            self._send(200, EXAMPLE_PROJECT.read_bytes(), "application/json; charset=utf-8")
        elif path == "/api/examples/registry-readout":
            self._send(200, files("friskoli_cad").joinpath("examples", "registry_readout.project.json").read_bytes(),
                       "application/json; charset=utf-8")
        elif path == "/api/modules":
            self._json(200, {"protocol_version": "0.1.0", "modules": default_registry().manifests})
        elif path == "/api/catalog":
            self._json(200, default_registry().catalog)
        elif path == "/api/capabilities":
            self._json(200, {
                "api_version": "0.2.0", "workspace_versions": ["0.1.0", "0.2.0", "0.3.0"],
                "catalog_versions": ["0.1.0"], "execution_semantics": "legacy-explicit-v1",
                "project_versions": ["0.1.0", "0.2.0"], "replay_versions": ["0.1.0"],
                "execution": {"mode": "synchronous", "pause": False, "resume": False, "partial_results": False},
                "limits": {"request_bytes": MAX_REQUEST_BYTES, "cells": MAX_VIEW_CELLS,
                           "xy_tiles": MAX_VIEW_TILES, "replay_values": MAX_REPLAY_VALUES, "steps": 100},
                "placeables": [{"kind": item["kind"], "module": item["initializer"]["module"]}
                               for item in default_registry().catalog["objects"]],
            })
        elif path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            body = files("friskoli_cad").joinpath("web", filename).read_bytes()
            self._send(200, body, content_type)
        else:
            self._json(404, {"error": {"code": "http.not_found", "message": "unknown path"}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path not in ("/api/replay", "/api/validate"):
            self._json(404, {"error": {"code": "http.not_found", "message": "unknown path"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ReplayRequestError("request.size", "request size must be between 1 and 1000000 bytes", 413)
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict) or not {"project", "dt_s", "steps"} <= set(request) or set(request) - {"project", "dt_s", "steps", "request_id"}:
                raise ReplayRequestError("request.shape", "expected project, dt_s and steps")
            request_id = request.get("request_id")
            if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128):
                raise ReplayRequestError("request.id", "request_id must be a short string")
            if self.path == "/api/validate":
                simulation = prepare_project(request["project"], dt_s=request["dt_s"], steps=request["steps"])
                self._json(200, {"api_version": "0.2.0", "valid": True, "issues": [],
                                 "cells": len(simulation.current.cell_frame["cells"]),
                                 "voxels": simulation.current.domain.voxel_count})
                return
            replay = build_replay(request["project"], dt_s=request["dt_s"], steps=request["steps"])
            replay["execution"] = {"api_version": "0.2.0", "request_id": request_id, "status": "completed",
                                   "dt_s": request["dt_s"], "steps": request["steps"],
                                   "project_sha256": hashlib.sha256(json.dumps(request["project"], sort_keys=True,
                                       separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()}
            self._json(200, replay)
        except (ReplayRequestError, ProtocolError, SimulationError, ValueError, TypeError, KeyError) as error:
            status = error.status if isinstance(error, ReplayRequestError) else 422
            code = getattr(error, "code", "request.invalid")
            self._json(status, {"error": {"code": code, "path": getattr(error, "path", "/"), "message": str(error)}})


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local Friskoli-CAD replay viewer")
    parser.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", arguments.port), ReplayHandler)
    print(f"Friskoli-CAD replay: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
