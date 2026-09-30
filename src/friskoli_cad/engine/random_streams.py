"""Versioned, order-independent PCG64 streams with complete JSON checkpoints."""
from __future__ import annotations

import hashlib
import json
import math
from numbers import Integral

import numpy as np


STREAM_VERSION = "pcg64-sha256-key-v1"


def _identity(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError(f"{label} must not contain Unicode surrogate code points")
    return value


class RandomStream:
    """A deliberately small distribution API using explicit raw-bit transforms."""

    def __init__(self, bit_generator):
        self.bit_generator = bit_generator

    def random_raw(self):
        return int(self.bit_generator.random_raw())

    def uniform_open(self):
        # 52 random bits, midpoint bins: neither endpoint is representable.
        return ((self.random_raw() >> 12) + 0.5) * 2.0**-52

    def exponential(self, rate_s):
        if not math.isfinite(rate_s) or rate_s <= 0:
            raise ValueError("exponential rate must be finite and positive")
        result = -math.log(self.uniform_open()) / rate_s
        if not math.isfinite(result) or result <= 0:
            raise ValueError("exponential waiting time exceeds float64 precision")
        return result


class RandomStreams:
    """Run/node/group/stable-cell/purpose namespaces independent of creation order.

    Clone once before a proposed time step; commit by replacing the old owner only
    after all downstream checks pass. Drawing mutates this instance, never clones.
    """

    def __init__(self, run_seed):
        if isinstance(run_seed, bool) or not isinstance(run_seed, Integral) or run_seed < 0:
            raise ValueError("run_seed must be a nonnegative integer")
        self.run_seed = int(run_seed)
        self._streams = {}

    def stream(self, node_id, group_id, cell_id, purpose):
        key = tuple(_identity(value, name) for value, name in zip(
            (node_id, group_id, cell_id, purpose),
            ("node_id", "group_id", "cell_id", "purpose")))
        if key not in self._streams:
            encoded = json.dumps([STREAM_VERSION, str(self.run_seed), *key],
                                 ensure_ascii=True, separators=(",", ":")).encode("ascii")
            seed = int.from_bytes(hashlib.sha256(encoded).digest(), "big")
            self._streams[key] = RandomStream(np.random.PCG64(seed))
        return self._streams[key]

    def to_dict(self):
        entries = []
        for key, stream in sorted(self._streams.items()):
            state = stream.bit_generator.state
            entries.append({"key": list(key), "state": {
                "bit_generator": "PCG64",
                "state": {"state": format(state["state"]["state"], "032x"),
                          "inc": format(state["state"]["inc"], "032x")},
                "has_uint32": int(state["has_uint32"]),
                "uinteger": int(state["uinteger"]),
            }})
        return {"version": STREAM_VERSION, "numpy_version": np.__version__,
                "run_seed": str(self.run_seed), "streams": entries}

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or payload.get("version") != STREAM_VERSION:
            raise ValueError("Unsupported random stream checkpoint version")
        if payload.get("numpy_version") != np.__version__:
            raise ValueError("Random checkpoint requires the identical NumPy version")
        seed = payload.get("run_seed")
        if not isinstance(seed, str) or not seed.isascii() or not seed.isdecimal():
            raise ValueError("Checkpoint run_seed must be a decimal string")
        result = cls(int(seed))
        if not isinstance(payload.get("streams"), list):
            raise ValueError("Checkpoint streams must be a list")
        for entry in payload["streams"]:
            try:
                key = entry["key"]
                if not isinstance(key, list) or len(key) != 4:
                    raise ValueError("Checkpoint stream key must have four identities")
                key = tuple(_identity(value, "stream key") for value in key)
                if key in result._streams:
                    raise ValueError("Duplicate stream key")
                encoded = entry["state"]
                if encoded["bit_generator"] != "PCG64":
                    raise ValueError("Checkpoint bit generator must be PCG64")
                restored = {}
                for name in ("state", "inc"):
                    value = encoded["state"][name]
                    if not isinstance(value, str) or len(value) != 32 or any(
                            c not in "0123456789abcdef" for c in value):
                        raise ValueError("PCG64 state must be a 128-bit lowercase hex string")
                    restored[name] = int(value, 16)
                if restored["inc"] % 2 != 1:
                    raise ValueError("PCG64 increment must be odd")
                cache = encoded["has_uint32"], encoded["uinteger"]
                if any(type(v) is not int for v in cache) or cache[0] not in (0, 1) or not 0 <= cache[1] < 2**32:
                    raise ValueError("Invalid PCG64 uint32 cache")
                generator = np.random.PCG64(0)
                generator.state = {"bit_generator": "PCG64", "state": restored,
                                   "has_uint32": cache[0], "uinteger": cache[1]}
                result._streams[key] = RandomStream(generator)
            except (KeyError, TypeError) as error:
                raise ValueError("Malformed random stream checkpoint") from error
        return result

    def clone(self):
        return self.from_dict(self.to_dict())
