"""Bounded, self-contained checkpoint files for committed spatial simulations.

The file contains its frozen project and the version-locked numerical state.
It does not resume a task, import executable plugins, or migrate implementations.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import tempfile

from jsonschema import Draft202012Validator

from friskoli_cad.protocol.task_validation import (
    TaskValidationError, canonical_bytes, strict_json_loads,
)
from .spatial_checkpoint import export_checkpoint, restore_checkpoint


FILE_VERSION = "0.1.0"
MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024


class CheckpointFileError(ValueError):
    """A file/encoding rejection; numerical errors retain SimulationError codes."""

    def __init__(self, code, message, path=""):
        self.code, self.path = code, str(path)
        super().__init__(message)


def _limit(value):
    if type(value) is not int or value <= 0:
        raise CheckpointFileError("checkpoint.limit", "max_bytes must be a positive integer")
    return value


@lru_cache(maxsize=1)
def _validator():
    schema = json.loads(files("friskoli_cad.protocol").joinpath(
        "schemas", "checkpoint-file-v0.1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def checkpoint_document(sim):
    """Capture a self-contained JSON document at a committed step boundary.

    The caller must own the simulation; do not call concurrently with step().
    The returned document shares no mutable data with the simulation.
    """
    checkpoint = export_checkpoint(sim)
    document = {"checkpoint_file_version": FILE_VERSION,
                "project": deepcopy(sim.project), "checkpoint": checkpoint}
    document["document_sha256"] = _digest(document)
    return document


def save_checkpoint(sim, path, *, overwrite=False, max_bytes=MAX_CHECKPOINT_BYTES):
    """Flush a sibling temporary file and publish it atomically.

    Existing destinations are protected by an atomic hard-link operation unless
    overwrite=True explicitly requests replacement. Filesystems without hard
    links cannot use the default mode. No partial file is published. This is not
    a guarantee against power loss or hardware/storage failure.
    """
    limit = _limit(max_bytes)
    if type(overwrite) is not bool:
        raise CheckpointFileError("checkpoint.overwrite", "overwrite must be a boolean", path)
    target = Path(path).absolute()
    document = checkpoint_document(sim)
    # Preserve JSON floating-point tokens: JCS hashes can emit large binary64
    # values as integer tokens, which the strict external JSON reader rejects.
    data = (json.dumps(document, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > limit:
        raise CheckpointFileError("checkpoint.too_large", "Checkpoint exceeds the file byte limit", target)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent,
                prefix=f".{target.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)
    except FileExistsError as error:
        raise CheckpointFileError("checkpoint.exists",
            "Destination exists; use a new path or explicitly request overwrite", target) from error
    except OSError as error:
        raise CheckpointFileError("checkpoint.write", f"Cannot publish checkpoint: {error}", target) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"path": str(target), "bytes": len(data),
            "document_sha256": document["document_sha256"],
            "frame_index": document["checkpoint"]["frame_index"],
            "time_s": document["checkpoint"]["time_s"]}


def load_checkpoint(path, registry=None, *, max_bytes=MAX_CHECKPOINT_BYTES):
    """Read at most max_bytes + 1 bytes, verify hashes/locks, then restore state.

    Checksums detect accidental corruption, not authorship. A custom adapter
    must already be explicitly installed and registered by the caller.
    """
    limit = _limit(max_bytes)
    source = Path(path).absolute()
    try:
        with source.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as error:
        raise CheckpointFileError("checkpoint.read", f"Cannot read checkpoint: {error}", source) from error
    if len(raw) > limit:
        raise CheckpointFileError("checkpoint.too_large", "Checkpoint exceeds the file byte limit", source)
    try:
        document = strict_json_loads(raw)
        error = next(_validator().iter_errors(document), None)
        if error is not None:
            pointer = "/" + "/".join(str(p) for p in error.absolute_path)
            raise CheckpointFileError("checkpoint.file_schema", f"{pointer}: {error.message}", source)
        content = {key: value for key, value in document.items() if key != "document_sha256"}
        if _digest(content) != document["document_sha256"]:
            raise CheckpointFileError("checkpoint.file_hash", "Checkpoint document checksum differs", source)
    except TaskValidationError as error:
        raise CheckpointFileError("checkpoint.json", str(error), source) from error
    return restore_checkpoint(document["project"], document["checkpoint"], registry)
