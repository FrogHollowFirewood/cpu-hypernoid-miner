# SPDX-License-Identifier: Apache-2.0
# Modified for CPU HyperNOID Miner v1.0.0 from the preserved Parano1d protocol tests.
import json
import os
from pathlib import Path
from queue import Queue
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from noid_stratum import Job, Miner, ProtocolError, UnsafeShareError, tls_context


NAMESPACE = "1234567890abcdef"
DOMAIN = "11" * 32
TARGET = "ff" * 31 + "7f"
HEADER = "00" * 256
USERNAME = "o1qqqqqqqqqqqqqqq.prototype"
ENGINE = """
import sys
old_epoch = None
for line in sys.stdin:
    if line.strip() == 'quit':
        break
    if line.strip() == 'stop':
        continue
    epoch, header, target, namespace = line.split()
    if old_epoch:
        print(old_epoch, 'ff' * 16, flush=True)
    print(epoch, int(epoch).to_bytes(8, 'little').hex() + namespace, flush=True)
    old_epoch = epoch
"""


def notify(job_id, **changes):
    return {"id": None, "method": "mining.notify", "params": [{
        "job_id": job_id, "nonce_field_index": 10, "nonce_bits": 64,
        "nonce_prefix_hex": NAMESPACE, "pow_fields_hex": HEADER,
        "share_target_hex": TARGET, "work_domain_id": DOMAIN,
        "expires_in_seconds": 5, "clean": True, **changes,
    }]}


def send(sock, *objects):
    lines = "".join(json.dumps(obj) + "\n" for obj in objects).encode()
    # Fragment one message and concatenate the remainder with later messages.
    sock.sendall(lines[:5])
    sock.sendall(lines[5:])


class ProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cert = Path(cls.temp.name) / "cert.pem"
        key = Path(cls.temp.name) / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                       check=True, capture_output=True)
        cls.server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_tls.load_cert_chain(cert, key)
        cls.client_tls = ssl.create_default_context(cafile=str(cert))
        cls.cert = cert

    def test_untrusted_certificate_and_wrong_hostname_are_rejected(self):
        for context, hostname in [(tls_context(), 'localhost'), (tls_context(str(self.cert)), 'wrong.example')]:
            with self.subTest(hostname=hostname), socket.socket() as listener:
                listener.bind(('127.0.0.1', 0)); listener.listen(); listener.settimeout(5)
                failures = Queue()
                def server():
                    try:
                        with listener.accept()[0] as incoming:
                            incoming.settimeout(5)
                            with self.server_tls.wrap_socket(incoming, server_side=True): pass
                    except ssl.SSLError: pass  # The client correctly aborts its rejected handshake.
                    except Exception as exc: failures.put(exc)
                thread = threading.Thread(target=server, daemon=True); thread.start()
                with socket.create_connection(('127.0.0.1', listener.getsockname()[1]), timeout=5) as incoming:
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        context.wrap_socket(incoming, server_hostname=hostname)
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                if not failures.empty(): raise failures.get_nowait()

    def exercise_session(self, pool_script, client_exception, engine_args=None):
        errors = Queue()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]

            def pool():
                try:
                    with listener.accept()[0] as incoming:
                        incoming.settimeout(5)
                        with self.server_tls.wrap_socket(incoming, server_side=True) as conn:
                            with conn.makefile("rb") as reader:
                                pool_script(conn, reader)
                except Exception as exc:
                    errors.put(exc)

            thread = threading.Thread(target=pool, daemon=True)
            thread.start()
            with subprocess.Popen(engine_args or [sys.executable, "-c", ENGINE], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1) as engine:
                miner = Miner(engine)
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
                        with self.client_tls.wrap_socket(client, server_hostname="localhost") as conn:
                            with self.assertRaises(client_exception):
                                miner.session(conn, USERNAME)
                finally:
                    miner.engine_command("quit")
                    engine.wait(timeout=5)
                    thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                if not errors.empty():
                    raise errors.get_nowait()
                return miner

    def authorize(self, conn, reader, initial_job=None):
        request = json.loads(reader.readline())
        self.assertEqual(request["method"], "mining.subscribe")
        send(conn, {"id": request["id"], "error": None, "result": {
            "protocol": "parano1d-stratum-v1", "nonce_bits": 64,
            "session_namespace": NAMESPACE,
        }}, initial_job or notify("initial"))
        request = json.loads(reader.readline())
        self.assertEqual(request["method"], "mining.authorize")
        self.assertEqual(request["params"], [USERNAME, "x"])
        send(conn, {"id": request["id"], "error": None, "result": True})

    def read_share(self, reader, job, epoch):
        request = json.loads(reader.readline())
        self.assertEqual(request["method"], "mining.submit")
        self.assertEqual(request["params"], [job, epoch.to_bytes(8, "little").hex() + NAMESPACE, DOMAIN])
        return request["id"]

    def test_tls_accepts_and_invalidates_old_engine_work(self):
        def pool(conn, reader):
            self.authorize(conn, reader)
            request_id = self.read_share(reader, "initial", 1)
            send(conn, {"id": request_id, "error": None, "result": True},
                 {"id": None, "method": "mining.pause", "params": []}, notify("next"))
            request_id = self.read_share(reader, "next", 2)
            send(conn, {"id": request_id, "error": None, "result": True})

        miner = self.exercise_session(pool, ConnectionError)
        self.assertEqual((miner.accepted, miner.stale, miner.rejected), (2, 0, 0))
        self.assertEqual(miner.accepted_work, 4)
        self.assertIsNone(miner.job)

    def test_stale_and_invalid_shares_are_distinct(self):
        def pool(conn, reader):
            self.authorize(conn, reader)
            request_id = self.read_share(reader, "initial", 1)
            send(conn, {"id": request_id, "error": {"message": "stale share"}, "result": None}, notify("next"))
            request_id = self.read_share(reader, "next", 2)
            send(conn, {"id": request_id, "error": {"message": "rejected (below share target)"}, "result": None})

        miner = self.exercise_session(pool, UnsafeShareError)
        self.assertEqual((miner.accepted, miner.stale, miner.rejected), (0, 1, 1))

    def test_rejects_bad_job_fields(self):
        for key, value in [("nonce_prefix_hex", "00" * 8), ("share_target_hex", "00" * 32),
                           ("nonce_bits", 128), ("pow_fields_hex", "00"), ("work_domain_id", "00")]:
            with self.subTest(key=key):
                message = notify("one")
                message["params"][0][key] = value
                with self.assertRaises(ProtocolError):
                    Job.parse(message["params"], NAMESPACE)

    @unittest.skipUnless(os.environ.get("NOID_TEST_WORKER"), "set NOID_TEST_WORKER to the compiled C worker")
    def test_compiled_worker_finds_reference_share_and_advances_on_retarget(self):
        binary = Path(os.environ["NOID_TEST_WORKER"]).resolve(strict=True)
        # First hit of the Apache-2.0 gacanga/NoidMiner scalar reference, scanning
        # the Suprnova all-zero-fields fixture with this session namespace.
        # The reference matched the published nonce 0 and 1 digests before use.
        target = (1 << 243).to_bytes(32, "little").hex()
        first_nonce = "3c100000000000001234567890abcdef"
        first_digest = "bb3957ef6d5ea71d7f0d4c6a15459eca0fae5d46d4de08771dc7eaee6d240000"
        self.assertLess(int.from_bytes(bytes.fromhex(first_digest), "little"), 1 << 243)
        self.assertEqual(subprocess.check_output([str(binary), "--digest", HEADER[:320] + first_nonce + HEADER[352:]],
                                                 text=True).strip(), first_digest)
        updated_target = (9 << 240).to_bytes(32, "little").hex()
        next_domain = "22" * 32

        def pool(conn, reader):
            self.authorize(conn, reader, notify("initial", share_target_hex=target))
            request = json.loads(reader.readline())
            self.assertEqual(request["method"], "mining.submit")
            self.assertEqual(request["params"], ["initial", first_nonce, DOMAIN])
            send(conn, {"id": request["id"], "error": None, "result": True},
                 notify("retarget", share_target_hex=updated_target, work_domain_id=next_domain))

            last_counter = int.from_bytes(bytes.fromhex(first_nonce[:16]), "little")
            for line in reader:
                request = json.loads(line)
                self.assertEqual(request["method"], "mining.submit")
                job, nonce, domain = request["params"]
                self.assertEqual(nonce[16:], NAMESPACE)
                counter = int.from_bytes(bytes.fromhex(nonce[:16]), "little")
                self.assertGreater(counter, last_counter)
                last_counter = counter
                if job == "initial":
                    self.assertEqual(domain, DOMAIN)
                    continue
                self.assertEqual((job, domain), ("retarget", next_domain))
                digest = subprocess.check_output([str(binary), "--digest", HEADER[:320] + nonce + HEADER[352:]],
                                                 text=True).strip()
                self.assertLess(int.from_bytes(bytes.fromhex(digest), "little"), 9 << 240)
                send(conn, {"id": request["id"], "error": None, "result": True},
                     {"id": None, "method": "client.reconnect", "params": []})
                reader.read()
                return
            self.fail("worker never submitted on the new job")

        miner = self.exercise_session(pool, ConnectionError, [str(binary), "--worker", "1"])
        self.assertEqual((miner.accepted, miner.stale, miner.rejected), (2, 0, 0))
        self.assertEqual(miner.accepted_work, 8191 + ((1 << 256) - 1) // (9 << 240))


if __name__ == "__main__":
    unittest.main()
