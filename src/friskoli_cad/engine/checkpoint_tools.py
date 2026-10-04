"""Version locks and hashes for the current modular checkpoint format."""
import hashlib
import platform
from pathlib import Path
import numpy as np
import rfc8785

def _hash(value):
    from friskoli_cad.tasks.arrays import hashable
    return hashlib.sha256(rfc8785.dumps(hashable(value))).hexdigest()


def _validator_record(validator):
    return {"run_id": validator.run_id, "next_index": validator.next_index,
        "previous_time": validator.previous_time, "frame_version": validator.frame_version,
        "alive": dict(validator.alive), "seen": sorted(validator.seen)}


def _implementation_lock(registry, field_backend='numpy-cpu'):
    package = Path(__file__).resolve().parents[1]
    paths = [package / "project.py", package / "registry.py"]
    for directory in ("engine", "science", "protocol"):
        paths.extend((package / directory).rglob("*.py"))
    for directory in ("engine/declarations", "science/data"):
        paths.extend((package / directory).glob("*.json"))
    source_hashes = {path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sorted(set(paths))}
    from .field_backend import backend_environment
    return {"source_sha256": source_hashes, "catalog_sha256": _hash(registry.catalog),
            **({'backend_environment': backend_environment(field_backend)} if field_backend != 'numpy-cpu' else {}),
            "numpy_version": np.__version__, "python_version": platform.python_version(),
            "machine": platform.machine(), "system": platform.system()}
