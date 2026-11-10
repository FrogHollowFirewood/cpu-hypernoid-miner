"""Independent worker scheduling/accounting checks using a deterministic slow kernel.

These tests exercise the real worker.c without relying on cryptographic luck.
The regular vector and TLS tests continue to validate the actual hash kernel.
"""

import os
from pathlib import Path
from queue import Queue, Empty
import re
import subprocess
import tempfile
import threading
import time
import unittest


STUB = r'''
#define _POSIX_C_SOURCE 200809L
#include "noid.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
int noid_avx512_supported(void) { return 1; }
void noid_prepare(noid_job *job, const uint8_t fields[256])
{ (void)fields; memset(job, 0, sizeof(*job)); }
void noid_hash_reference(const uint8_t fields[256], uint8_t hash[32])
{ (void)fields; memset(hash, 0, 32); }
int noid_below_target(const uint8_t digest[32], const uint8_t target[32])
{ (void)digest; (void)target; return 1; }
static void delay(void)
{
    const char *value = getenv("NOID_TEST_DELAY_MS");
    long ms = value ? strtol(value, NULL, 10) : 3;
    struct timespec t = { .tv_sec = ms / 1000, .tv_nsec = ms % 1000 * 1000000 };
    nanosleep(&t, NULL);
}
void noid_hash_batch(const noid_job *job, const uint8_t nonces[NOID_BATCH][16],
                     uint8_t hashes[NOID_BATCH][32])
{ (void)job; (void)nonces; memset(hashes, 0, NOID_BATCH * 32); }
void noid_hash_range(const noid_job *job, uint64_t first, const uint8_t ns[8],
                     uint8_t hashes[NOID_BATCH][32])
{ (void)job; (void)first; (void)ns; delay(); memset(hashes, 0, NOID_BATCH * 32); }
void noid_scan_range(const noid_job *job, uint64_t first, const uint8_t ns[8],
                     const uint8_t target[32], uint8_t hashes[NOID_BATCH][32],
                     uint8_t matches[NOID_BATCH])
{
    (void)target;
    noid_hash_range(job, first, ns, hashes);
    memset(matches, 0, NOID_BATCH);
    matches[0] = 1;
}
'''


class WorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(__file__).resolve().parent
        stub = Path(cls.temp.name) / "kernel.c"
        cls.binary = Path(cls.temp.name) / "worker"
        stub.write_text(STUB)
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-O2", "-Wall", "-Wextra",
                        "-pthread", "-I", str(source), str(source / "worker.c"), str(stub),
                        "-o", str(cls.binary)], check=True, capture_output=True)

    def test_benchmark_elapsed_includes_inflight_batches(self):
        for mode in ("--bench", "--bench-scan"):
            with self.subTest(mode=mode):
                output = subprocess.check_output([str(self.binary), mode, "2", "1"], text=True,
                        env={**os.environ, "NOID_TEST_DELAY_MS": "1250"}, timeout=5)
                match = re.search(r"(\d+) hashes \| ([0-9.]+) s", output)
                self.assertIsNotNone(match)
                self.assertEqual(int(match.group(1)), 128)
                self.assertGreaterEqual(float(match.group(2)), 1.24)

    def test_four_workers_do_not_reuse_nonce_ranges_after_retarget(self):
        output = Queue()
        namespace = "1234567890abcdef"
        with subprocess.Popen([str(self.binary), "--worker", "4"], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, bufsize=1) as engine:
            def read():
                for line in engine.stdout:
                    output.put(line)
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            all_nonces = set()
            per_lane = {}
            try:
                for epoch in (1, 2):
                    engine.stdin.write(f"{epoch} {'00'*256} {'ff'*32} {namespace}\n")
                    engine.stdin.flush()
                    observed = 0
                    residues = set()
                    deadline = time.monotonic() + 5
                    while observed < 128:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            self.fail("worker did not emit enough test nonces")
                        try:
                            line = output.get(timeout=remaining)
                        except Empty:
                            self.fail("worker output timed out")
                        seen_epoch, nonce = line.split()
                        self.assertNotIn(nonce, all_nonces)
                        all_nonces.add(nonce)
                        self.assertEqual(nonce[16:], namespace)
                        counter = int.from_bytes(bytes.fromhex(nonce[:16]), "little")
                        residue = counter % 256
                        self.assertIn(residue, (0, 64, 128, 192))
                        self.assertGreater(counter, per_lane.get(residue, -1))
                        per_lane[residue] = counter
                        if int(seen_epoch) == epoch:
                            residues.add(residue)
                            observed += 1
                    self.assertEqual(residues, {0, 64, 128, 192})
            finally:
                engine.stdin.write("quit\n")
                engine.stdin.flush()
                engine.wait(timeout=5)
                reader.join(timeout=5)
            self.assertEqual(engine.returncode, 0)
            self.assertEqual(engine.stderr.read(), "")


if __name__ == "__main__":
    unittest.main()
