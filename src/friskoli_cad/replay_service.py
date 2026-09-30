"""Small local HTTP service for protocol-based simulation replay."""

from __future__ import annotations

import argparse
import json
import math
import hashlib
import os
import re
import socket
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qs, urlsplit

from friskoli_cad.engine import SimulationError
from friskoli_cad.engine.modules import default_registry
from friskoli_cad.project import simulation_from_project
from friskoli_cad.project import validate_project
from friskoli_cad.protocol import ProtocolError, validate_frame_sequence
from friskoli_cad.protocol.task_validation import TaskValidationError, strict_json_loads
from friskoli_cad.tasks import TaskError, TaskService


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
    "/task-store.mjs": ("task-store.mjs", "text/javascript; charset=utf-8"),
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
    def _send(self, status: int, body: bytes, content_type: str, headers: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: object, headers: Mapping[str, str] | None = None) -> None:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", headers)

    def _task_service(self) -> TaskService:
        service = getattr(self.server, "task_service", None)
        if service is None:
            raise TaskError(503, "task.unavailable", "asynchronous task service is not enabled")
        return service

    def _task_error(self, error: TaskError) -> None:
        headers = {"Retry-After": "1"} if error.status in (429, 503) else None
        self._json(error.status, error.to_dict(), headers)

    def _task_body(self, maximum: int) -> bytes:
        if self.headers.get("Transfer-Encoding") is not None:
            raise TaskError(400, "task.request_invalid", "a single Content-Length is required")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,18}", lengths[0]):
            raise TaskError(400, "task.request_invalid", "a single integer Content-Length is required")
        length = int(lengths[0])
        if length < 1 or length > maximum:
            raise TaskError(413, "task.resource_limit", "request bytes exceed the published input limit")
        if self.headers.get_content_type() != "application/json":
            raise TaskError(415, "task.content_type", "Content-Type must be application/json")
        self.connection.settimeout(10)
        try:
            body = self.rfile.read(length)
        except (socket.timeout, OSError) as error:
            raise TaskError(400, "task.request_invalid", "request body was not received completely") from error
        if len(body) != length:
            raise TaskError(400, "task.request_invalid", "request body was not received completely")
        return body

    def _get_task(self, path: str, query: str) -> None:
        try:
            service = self._task_service()
            match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]{1,128})(?:/(input|events|result|chunks/([A-Za-z0-9_-]{1,128})))?", path)
            if match is None:
                raise TaskError(404, "task.not_found", "unknown task resource")
            run_id, resource, chunk_id = match.groups()
            if resource == "events":
                try:
                    params = parse_qs(query, keep_blank_values=True, strict_parsing=True) if query else {}
                except ValueError as error:
                    raise TaskError(400, "task.cursor_invalid", "invalid event query") from error
                if set(params) - {"after", "limit"} or any(len(values) != 1 for values in params.values()):
                    raise TaskError(400, "task.cursor_invalid", "event query accepts one after and one limit")
                if any(not re.fullmatch(r"[0-9]{1,16}", values[0]) for values in params.values()):
                    raise TaskError(400, "task.cursor_invalid", "event cursors and limits must be non-negative integers")
                page_size = service.capabilities()["limits"]["event_page_size"]
                result = service.events(run_id, after=int(params.get("after", ["0"])[0]),
                                        limit=int(params.get("limit", [str(page_size)])[0]))
            elif query:
                raise TaskError(400, "task.request_invalid", "this resource does not accept query parameters")
            elif resource is None:
                result = service.get(run_id)
            elif resource == "input":
                result = service.input(run_id)
            elif resource == "result":
                result = service.manifest(run_id)
            else:
                self._send(200, service.chunk(run_id, chunk_id), "application/json; charset=utf-8")
                return
            self._json(200, result)
        except TaskError as error:
            self._task_error(error)

    def _post_task(self, path: str, query: str) -> None:
        try:
            service = self._task_service()
            if query:
                raise TaskError(400, "task.request_invalid", "task mutations do not accept query parameters")
            if path == "/api/runs":
                keys = self.headers.get_all("Idempotency-Key", [])
                if len(keys) != 1 or not keys[0].strip():
                    raise TaskError(400, "task.idempotency_key", "a single nonempty Idempotency-Key is required")
                maximum = service.capabilities()["limits"]["request_bytes"]
                try:
                    submission = strict_json_loads(self._task_body(maximum))
                except TaskValidationError as error:
                    self._json(400, {"task_contract_version": "0.1.0", "issues": error.issues})
                    return
                task, created = service.submit(submission, keys[0])
                self._json(202 if created else 200, task, {"Location": f"/api/runs/{task['run_id']}"})
                return
            match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]+)/cancel", path)
            if match is None:
                raise TaskError(404, "task.not_found", "unknown task resource")
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0":
                raise TaskError(400, "task.request_invalid", "cancel does not accept a request body")
            task, accepted = service.cancel(match.group(1))
            self._json(202 if accepted else 200, task)
        except TaskValidationError as error:
            self._json(422, {"task_contract_version": "0.1.0", "issues": error.issues})
        except TaskError as error:
            self._task_error(error)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        target = urlsplit(self.path)
        path = target.path
        if path == "/api/runs" or path.startswith("/api/runs/"):
            self._get_task(path, target.query)
            return
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
            capabilities = {
                "api_version": "0.2.0", "workspace_versions": ["0.1.0", "0.2.0", "0.3.0"],
                "catalog_versions": ["0.1.0"], "execution_semantics": "legacy-explicit-v1",
                "project_versions": ["0.1.0", "0.2.0"], "replay_versions": ["0.1.0"],
                "execution": {"mode": "synchronous", "pause": False, "resume": False, "partial_results": False},
                "limits": {"request_bytes": MAX_REQUEST_BYTES, "cells": MAX_VIEW_CELLS,
                           "xy_tiles": MAX_VIEW_TILES, "replay_values": MAX_REPLAY_VALUES, "steps": 100},
                "placeables": [{"kind": item["kind"], "module": item["initializer"]["module"]}
                               for item in default_registry().catalog["objects"]],
            }
            service = getattr(self.server, "task_service", None)
            if service is not None:
                capabilities["task"] = service.capabilities()
            self._json(200, capabilities)
        elif path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            body = files("friskoli_cad").joinpath("web", filename).read_bytes()
            self._send(200, body, content_type)
        else:
            self._json(404, {"error": {"code": "http.not_found", "message": "unknown path"}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        target = urlsplit(self.path)
        if target.path == "/api/runs" or target.path.startswith("/api/runs/"):
            self._post_task(target.path, target.query)
            return
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


def default_task_directory() -> Path:
    """Keep durable local runs outside source checkouts."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "Friskoli-CAD" / "tasks"


class ReplayServer(ThreadingHTTPServer):
    """Local HTTP transport owns and closes its optional task service."""

    def __init__(self, address, *, task_directory: str | Path | None = None, task_limits=None):
        super().__init__(address, ReplayHandler)
        self.task_service = None
        if task_directory is not None:
            try:
                self.task_service = TaskService(task_directory, limits=task_limits)
            except BaseException:
                super().server_close()
                raise

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            if self.task_service is not None:
                self.task_service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local Friskoli-CAD replay viewer")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--task-dir", type=Path, default=default_task_directory(),
                        help="persistent task database and result directory (outside the repository by default)")
    parser.add_argument("--sync-only", action="store_true", help="serve only the legacy synchronous API")
    arguments = parser.parse_args()
    server = ReplayServer(("127.0.0.1", arguments.port),
                          task_directory=None if arguments.sync_only else arguments.task_dir)
    print(f"Friskoli-CAD replay: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
