"""Run or continue the modular profile and atomically save its complete state.

Usage: python -m friskoli_cad.checkpoint --example --steps 20 --dt 0.1 --output state.zip
       python -m friskoli_cad.checkpoint --resume state.zip --steps 30 --dt 0.1 --output next.zip
"""
from __future__ import annotations

import argparse
from importlib.resources import files
import json
import math
from pathlib import Path
import sys

from friskoli_cad.engine.task_checkpoint import load_task_checkpoint, save_task_checkpoint
MAX_CHECKPOINT_BYTES = 128 * 1024 * 1024
from friskoli_cad.engine.core import SimulationError
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import ProtocolError
from friskoli_cad.protocol.task_validation import strict_json_loads, TaskValidationError


def _steps(value):
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("steps must be an integer") from None
    if not 0 <= result <= 10000:
        raise argparse.ArgumentTypeError("steps must be between 0 and 10000")
    return result


def _dt(value):
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("dt must be a number") from None
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("dt must be finite and positive")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Save/restore a committed modular simulation; this does not resume a server task.")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--example", action="store_true", help="Use the bundled modular foundation example")
    inputs.add_argument("--project", type=Path, help="Start from a modular Project 0.6 JSON file")
    inputs.add_argument("--resume", type=Path, help="Continue a self-contained checkpoint file")
    parser.add_argument("--steps", required=True, type=_steps, help="Additional numerical steps (0..10000)")
    parser.add_argument("--dt", required=True, type=_dt, help="Numerical step in seconds; use the same dt sequence for exact comparison")
    parser.add_argument("--output", required=True, type=Path, help="Destination checkpoint file")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace an existing destination after successful completion")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() and not args.overwrite:
            raise ValueError("Output exists; choose a new path or use --overwrite")
        if args.resume:
            sim = load_task_checkpoint(args.resume, maximum=MAX_CHECKPOINT_BYTES)
        else:
            if args.example:
                raw = files("friskoli_cad").joinpath("examples", "modular_foundation.project.json").read_bytes()
            else:
                with args.project.open("rb") as stream:
                    raw = stream.read(MAX_CHECKPOINT_BYTES + 1)
            if len(raw) > MAX_CHECKPOINT_BYTES:
                raise ValueError("Project exceeds the file byte limit")
            project = strict_json_loads(raw)
            sim = simulation_from_project(project)
        start = {"frame_index": sim.frame_index, "time_s": sim.time_s}
        for _ in range(args.steps):
            sim.step(args.dt)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        receipt = save_task_checkpoint(sim, args.output, maximum=MAX_CHECKPOINT_BYTES)
        print(json.dumps({"status": "saved", "resumed": bool(args.resume),
            "start": start, "additional_steps": args.steps, "checkpoint": receipt}, ensure_ascii=True))
    except (ValueError, SimulationError, ProtocolError, TaskValidationError, OSError) as error:
        print(json.dumps({"status": "failed", "error": {"code": getattr(error, "code", "checkpoint.invalid"),
            "path": str(getattr(error, "path", "")), "message": str(error)}}, ensure_ascii=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
