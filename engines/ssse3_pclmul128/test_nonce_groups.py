"""Behavioral checks for grouped nonce ownership, retargeting and exhaustion."""
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
import tempfile
import threading
import time
import unittest

from test_worker import STUB

# Modified for CPU HyperNOID Miner v1.0.0: bounded scheduling allowance; assertions are unchanged.
NONCE_TEST_TIMEOUT = int(os.environ.get("NONCE_TEST_TIMEOUT", "60"))
if not 1 <= NONCE_TEST_TIMEOUT <= 300:
    raise ValueError("NONCE_TEST_TIMEOUT must be an integer from 1 to 300 seconds")


class NonceGroupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.source = Path(__file__).resolve().parent
        cls.directory = Path(cls.temp.name)
        # Every nonce is a deterministic valid share, exposing all lane and
        # chunk boundaries rather than only the first lane of each batch.
        cls.stub = cls.directory / 'kernel.c'
        cls.stub.write_text(STUB.replace('matches[0] = 1;',
                                        'memset(matches, 1, NOID_BATCH);'))
        cls.binaries = {}
        for group in (1, 4):
            binary = cls.directory / f'worker-group{group}'
            cls.compile(cls.source / 'worker.c', binary, group)
            cls.binaries[group] = binary

    @classmethod
    def compile(cls, source, binary, group):
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O2',
                        '-Wall', '-Wextra', '-pthread', f'-DNOID_BATCH_GROUP={group}',
                        '-I', str(cls.source), str(source), str(cls.stub),
                        '-o', str(binary)], check=True, capture_output=True)

    def test_all_lanes_and_groups_remain_disjoint_across_retargets(self):
        for group, binary in self.binaries.items():
            for threads in (4, 24, 32):
                with self.subTest(group=group, threads=threads):
                    self.check_retargets(group, binary, threads)

    def check_retargets(self, group, binary, threads):
        received = Queue()
        namespace = '1234567890abcdef'
        span = group * 64
        stride = threads * span
        with subprocess.Popen([str(binary), '--worker', str(threads)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, bufsize=1) as engine:
            def read():
                for line in engine.stdout:
                    received.put(line)
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            all_nonces = set()
            last_position = {}
            group_seen = {worker: set() for worker in range(threads)}
            try:
                for epoch in (1, 2, 3):
                    # The template and namespace stay identical while the
                    # target changes, which catches repeated ranges on retarget.
                    target = ('fe' if epoch == 2 else 'ff') * 32
                    engine.stdin.write(f'{epoch} {"00"*256} {target} {namespace}\n')
                    engine.stdin.flush()
                    current = [0] * threads
                    deadline = time.monotonic() + NONCE_TEST_TIMEOUT
                    while min(current) < span * 3:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            self.fail('Not all workers crossed three group boundaries')
                        try:
                            line = received.get(timeout=remaining)
                        except Empty:
                            self.fail('Timed out waiting for worker output')
                        observed_epoch, nonce = line.split()
                        self.assertNotIn(nonce, all_nonces)
                        all_nonces.add(nonce)
                        self.assertEqual(nonce[16:], namespace)
                        counter = int.from_bytes(bytes.fromhex(nonce[:16]), 'little')
                        worker = (counter // span) % threads
                        group_index = counter // stride
                        offset = counter % span
                        position = group_index * span + offset
                        # There may be gaps when a retarget invalidates a
                        # partially submitted batch, but there may be no reuse.
                        self.assertGreater(position, last_position.get(worker, -1))
                        last_position[worker] = position
                        group_seen[worker].add(group_index)
                        if int(observed_epoch) == epoch:
                            current[worker] += 1
                self.assertTrue(all(len(groups) >= 9 for groups in group_seen.values()))
            finally:
                engine.stdin.write('quit\n')
                engine.stdin.flush()
                engine.wait(timeout=5)
                reader.join(timeout=5)
            self.assertEqual(engine.returncode, 0)
            self.assertEqual(engine.stderr.read(), '')

    def test_cursor_exhaustion_never_wraps_or_mutates_failed_cursor(self):
        driver = self.directory / 'cursor.c'
        driver.write_text(r'''
#define main miner_main
#include "worker.c"
#undef main
#include <assert.h>
int main(void)
{
    struct nonce_cursor c = nonce_cursor_begin(3, 4);
    assert(c.base == 3 * UINT64_C(64) * NOID_BATCH_GROUP);
    for (unsigned i = 1; i < NOID_BATCH_GROUP; ++i) {
        assert(nonce_cursor_next(&c));
        assert(c.base == (3 * NOID_BATCH_GROUP + i) * UINT64_C(64));
    }
    assert(nonce_cursor_next(&c));
    assert(c.base == 7 * UINT64_C(64) * NOID_BATCH_GROUP);

    c.base = UINT64_MAX - 63;
    c.group_offset = NOID_BATCH_GROUP - 1;
    struct nonce_cursor before = c;
    assert(!nonce_cursor_next(&c));
    assert(c.base == before.base && c.group_offset == before.group_offset);
    if (NOID_BATCH_GROUP > 1) {
        c.base = UINT64_MAX - 127;
        c.group_offset = NOID_BATCH_GROUP - 2;
        assert(nonce_cursor_next(&c));
        assert(c.base == UINT64_MAX - 63);
        assert(!nonce_cursor_next(&c));
    }
    return 0;
}
''')
        for group in (1, 4):
            with self.subTest(group=group):
                binary = self.directory / f'cursor-group{group}'
                self.compile(driver, binary, group)
                subprocess.run([str(binary)], check=True, capture_output=True, timeout=3)


if __name__ == '__main__':
    unittest.main()
