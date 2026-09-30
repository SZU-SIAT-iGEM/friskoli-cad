import copy
import json
import unittest

import numpy as np
import rfc8785

from friskoli_cad.engine.random_streams import RandomStreams


class RandomStreamTests(unittest.TestCase):
    def test_order_and_unrelated_streams_do_not_change_cell_sequences(self):
        left, right = RandomStreams(487), RandomStreams(487)
        expected = {cell: [left.stream("move", "group", cell, "wait").random_raw()
                           for _ in range(8)] for cell in ("a", "b", "c")}
        for cell in ("c", "b", "a"):
            for _ in range(20):
                right.stream("other", "group", "unrelated", "wait").random_raw()
                right.stream("move", "group", cell, "report").random_raw()
            self.assertEqual(expected[cell], [right.stream("move", "group", cell, "wait").random_raw()
                                              for _ in range(8)])

    def test_every_key_component_and_seed_separate_streams(self):
        keys = [("n", "g", "c", "p"), ("n2", "g", "c", "p"),
                ("n", "g2", "c", "p"), ("n", "g", "c2", "p"), ("n", "g", "c", "p2")]
        streams = RandomStreams(42)
        draws = {streams.stream(*key).random_raw() for key in keys}
        draws.add(RandomStreams(43).stream(*keys[0]).random_raw())
        self.assertEqual(len(draws), 6)
        # Array encoding avoids ambiguous delimiter-based namespaces.
        self.assertNotEqual(streams.stream("a:b", "c", "d", "e").random_raw(),
                            streams.stream("a", "b:c", "d", "e").random_raw())

    def test_checkpoint_is_ijson_safe_and_restores_full_uint32_cache(self):
        streams = RandomStreams(2**160 + 7)
        stream = streams.stream("方向", "g", "c", "test")
        generator = np.random.Generator(stream.bit_generator)
        generator.integers(0, 100, dtype=np.uint32)
        payload = streams.to_dict()
        self.assertEqual(payload["streams"][0]["state"]["has_uint32"], 1)
        canonical = rfc8785.dumps(payload)
        restored = RandomStreams.from_dict(json.loads(canonical))
        self.assertEqual(restored.to_dict(), payload)
        other = np.random.Generator(restored.stream("方向", "g", "c", "test").bit_generator)
        np.testing.assert_array_equal(generator.integers(0, 2**32, 30, dtype=np.uint32),
                                      other.integers(0, 2**32, 30, dtype=np.uint32))
        self.assertEqual(streams.to_dict(), restored.to_dict())

    def test_rejected_candidate_does_not_consume_committed_streams(self):
        committed = RandomStreams(30)
        before = committed.to_dict()
        rejected = committed.clone()
        first = rejected.stream("n", "g", "c", "wait").uniform_open()
        self.assertEqual(committed.to_dict(), before)
        retried = committed.clone()
        self.assertEqual(retried.stream("n", "g", "c", "wait").uniform_open(), first)
        self.assertEqual(retried.to_dict(), rejected.to_dict())

    def test_uniform_is_strictly_open(self):
        stream = RandomStreams(10).stream("n", "g", "c", "p")
        samples = [stream.uniform_open() for _ in range(10000)]
        self.assertGreater(min(samples), 0.)
        self.assertLess(max(samples), 1.)

    def test_invalid_seed_identity_and_checkpoint_are_rejected(self):
        for seed in (-1, True, 1.5, "42", None):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                RandomStreams(seed)
        streams = RandomStreams(0)
        for key in (("", "g", "c", "p"), ("n", "g", 1, "p"), ("\ud800", "g", "c", "p")):
            with self.assertRaises(ValueError):
                streams.stream(*key)
        streams.stream("n", "g", "c", "p").random_raw()
        baseline = streams.to_dict()
        for key, value in (("version", "future"), ("numpy_version", "0.0"), ("run_seed", 23)):
            broken = copy.deepcopy(baseline)
            broken[key] = value
            with self.assertRaises(ValueError):
                RandomStreams.from_dict(broken)
        broken = copy.deepcopy(baseline)
        broken["streams"].append(broken["streams"][0])
        with self.assertRaises(ValueError):
            RandomStreams.from_dict(broken)
        for value in (10, "x" * 32, "0" * 33):
            broken = copy.deepcopy(baseline)
            broken["streams"][0]["state"]["state"]["state"] = value
            with self.assertRaises(ValueError):
                RandomStreams.from_dict(broken)


if __name__ == "__main__":
    unittest.main()
