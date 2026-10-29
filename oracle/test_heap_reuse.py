"""Check the oracle's heap ownership contract through the executable hooks."""
import os
import unittest

from oracle.render_oracle import RenderOracle
from oracle.stage1_oracle import FREE, MALLOC


@unittest.skipUnless(os.path.isfile("games/th13/th13.exe"), "original EXE unavailable")
class HeapReuseTests(unittest.TestCase):
    def setUp(self):
        self.oracle = RenderOracle("games/th13/th13.exe", "artifacts/reference/raw", "unused")
        self.oracle.install_overrides()
        self.machine = self.oracle.m
        self.imports = {imp.name.lower(): addr for addr, imp in self.machine.import_stubs.items()}

    def test_freed_blocks_split_coalesce_and_retain_bytes(self):
        alloc = lambda size: self.machine.call(MALLOC, [size])
        a, b, c = alloc(17), alloc(32), alloc(1)
        self.assertEqual((a % 16, b % 16, c % 16), (0, 0, 0))
        self.oracle.uc.mem_write(a, bytes(range(32)))
        self.machine.call(FREE, [a])
        self.machine.call(FREE, [b])
        small = alloc(16)
        self.assertEqual(small, a)
        self.assertEqual(bytes(self.oracle.uc.mem_read(small, 16)), bytes(range(16)))
        large = alloc(48)
        self.assertEqual(large, a + 16)
        self.assertEqual(bytes(self.oracle.uc.mem_read(large, 16)), bytes(range(16, 32)))
        self.assertNotEqual(c, large)
        self.machine.call(FREE, [0])
        with self.assertRaises(SystemExit):
            self.machine.call(FREE, [a + 1])

    def test_zero_size_is_allocated_and_double_free_is_detected(self):
        p = self.machine.call(MALLOC, [0])
        other = self.machine.call(MALLOC, [1])
        self.assertNotEqual(p, other)
        self.machine.call(FREE, [p])
        with self.assertRaises(SystemExit):
            self.machine.call(FREE, [p])

    def test_heap_zero_memory_does_not_zero_normal_allocation(self):
        malloc = self.imports["heapalloc"]
        free = self.imports["heapfree"]
        p = self.machine.call(malloc, [1, 0, 32])
        self.oracle.uc.mem_write(p, b"x" * 32)
        self.assertEqual(self.machine.call(free, [1, 0, p]), 1)
        q = self.machine.call(malloc, [1, 0, 32])
        self.assertEqual(q, p)
        self.assertEqual(bytes(self.oracle.uc.mem_read(q, 32)), b"x" * 32)
        self.assertEqual(self.machine.call(free, [1, 0, q]), 1)
        z = self.machine.call(malloc, [1, 8, 32])
        self.assertEqual(z, p)
        self.assertEqual(bytes(self.oracle.uc.mem_read(z, 32)), bytes(32))
        self.assertEqual(self.machine.call(free, [1, 0, 0]), 0)


if __name__ == "__main__":
    unittest.main()
