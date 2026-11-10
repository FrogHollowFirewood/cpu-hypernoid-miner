#!/usr/bin/env python3
"""Parano1d Stratum client for a separate CPU search process."""

import argparse
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from queue import Empty, Full, Queue
import re
import signal
import socket
import ssl
import subprocess
import threading
import time


LOG = logging.getLogger("noid-stratum")
MAX_LINE = 65536
MAX_PENDING = 128
HEX = re.compile(r"[0-9a-fA-F]+\Z")
WORKER = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
PAYOUT_ADDRESS = re.compile(r"o1[023456789acdefghjklmnpqrstuvwxyz]{10,90}\Z")


class ProtocolError(Exception):
    """The server returned a response outside the supported protocol."""


def hex_field(value, size):
    if not isinstance(value, str) or len(value) != 2 * size or not HEX.fullmatch(value):
        raise ProtocolError(f"expected a {size}-byte hex value")
    return value.lower()


@dataclass(frozen=True)
class Job:
    job_id: str
    domain: str
    fields: str
    target: str
    namespace: str
    expires_at: float

    @classmethod
    def parse(cls, params, namespace):
        if not isinstance(params, list) or len(params) != 1 or not isinstance(params[0], dict):
            raise ProtocolError("invalid mining.notify params")
        item = params[0]
        if item.get("nonce_field_index") != 10 or item.get("nonce_bits") != 64:
            raise ProtocolError("unsupported nonce layout")
        prefix = hex_field(item.get("nonce_prefix_hex"), 8)
        if prefix != namespace:
            raise ProtocolError("job nonce prefix does not match subscription")
        job_id = item.get("job_id")
        if not isinstance(job_id, str) or not 1 <= len(job_id) <= 256:
            raise ProtocolError("invalid job identifier")
        target = hex_field(item.get("share_target_hex"), 32)
        if not int(target, 16):
            raise ProtocolError("zero share target")
        expires = item.get("expires_in_seconds", 30)
        if type(expires) is not int or not 1 <= expires <= 86400:
            raise ProtocolError("invalid job expiry")
        return cls(job_id, hex_field(item.get("work_domain_id"), 32),
                   hex_field(item.get("pow_fields_hex"), 256), target, prefix,
                   time.monotonic() + expires)


class Miner:
    def __init__(self, engine):
        self.engine = engine
        self.epoch = 0
        self.job = None
        self.accepted = 0
        self.stale = 0
        self.rejected = 0
        self.accepted_work = 0
        self.candidates = Queue(maxsize=MAX_PENDING)
        self.failed = threading.Event()
        threading.Thread(target=self.read_candidates, name="engine-output", daemon=True).start()

    def engine_command(self, command):
        self.engine.stdin.write(command + "\n")
        self.engine.stdin.flush()

    def invalidate(self):
        if self.job is not None:
            self.job = None
            self.engine_command("stop")

    def publish(self, job):
        self.epoch += 1
        if job.expires_at <= time.monotonic():
            self.invalidate()
            return
        self.job = job
        self.engine_command(f"{self.epoch} {job.fields} {job.target} {job.namespace}")

    @staticmethod
    def send(connection, request_id, method, params):
        request = {"id": request_id, "method": method, "params": params}
        wire = json.dumps(request, separators=(",", ":")) + "\n"
        connection.sendall(wire.encode("ascii"))

    def read_candidates(self):
        try:
            while line := self.engine.stdout.readline(1024):
                parts = line.split()
                if len(parts) != 2 or not parts[0].isascii() or not parts[0].isdigit():
                    raise ProtocolError("invalid search engine response")
                epoch = int(parts[0])
                nonce = hex_field(parts[1], 16)
                self.candidates.put_nowait((epoch, nonce))
        except (OSError, ValueError, ProtocolError, Full) as exc:
            LOG.error("Search engine output failed: %s", exc)
        finally:
            self.failed.set()

    def submit_candidates(self, connection, pending, next_id):
        if self.job is not None and self.job.expires_at <= time.monotonic():
            self.invalidate()
        while True:
            try:
                epoch, nonce = self.candidates.get_nowait()
            except Empty:
                return next_id
            if self.job is None or self.epoch != epoch:
                continue
            if nonce[16:] != self.job.namespace:
                raise UnsafeShareError("search engine returned the wrong nonce namespace")
            if len(pending) >= MAX_PENDING:
                raise ProtocolError("too many unanswered share submissions")
            self.send(connection, next_id, "mining.submit",
                      [self.job.job_id, nonce, self.job.domain])
            target = int.from_bytes(bytes.fromhex(self.job.target), "little")
            pending[next_id] = ((1 << 256) - 1) // target
            next_id += 1

    def record_share(self, accepted, error, work):
        if accepted and not error:
            self.accepted += 1
            self.accepted_work += work
        else:
            reason = error.get("message", "") if isinstance(error, dict) else ""
            reason = reason.lower() if isinstance(reason, str) else ""
            if "stale" in reason or "superseded" in reason or "expired" in reason:
                self.stale += 1
            else:
                self.rejected += 1
                raise UnsafeShareError(f"non-stale share rejection ({self.rejected} total)")
        LOG.info("Shares accepted=%d stale=%d rejected=%d credited_work=%d",
                 self.accepted, self.stale, self.rejected, self.accepted_work)

    def session(self, connection, username):
        namespace = None
        authorized = False
        queued_job = None
        pending = {}
        next_id = 3
        buffer = bytearray()
        connected_at = last_rx = time.monotonic()
        next_report = connected_at + 30
        connection.settimeout(0.1)
        try:
            self.send(connection, 1, "mining.subscribe", ["noid-cpu-prototype/0.10.0-rig"])
            while not self.failed.is_set():
                now = time.monotonic()
                if now >= next_report:
                    self.engine_command("stats")
                    next_report = now + 30
                if now - last_rx >= 180 or (not authorized and now - connected_at >= 45):
                    raise TimeoutError("pool stopped responding")
                try:
                    data = connection.recv(16384)
                except socket.timeout:
                    next_id = self.submit_candidates(connection, pending, next_id)
                    continue
                if not data:
                    raise ConnectionError("pool closed the session")
                last_rx = time.monotonic()
                buffer.extend(data)
                while (newline := buffer.find(b"\n")) != -1:
                    if newline >= MAX_LINE:
                        raise ProtocolError("oversize Stratum message")
                    line = bytes(buffer[:newline])
                    del buffer[:newline + 1]
                    try:
                        message = json.loads(line)
                    except (UnicodeError, ValueError) as exc:
                        raise ProtocolError("invalid JSON from pool") from exc
                    if not isinstance(message, dict):
                        raise ProtocolError("Stratum message must be an object")
                    request_id = message.get("id")
                    result = message.get("result")
                    error = message.get("error")
                    method = message.get("method")
                    if request_id == 1 and namespace is None:
                        if error or not isinstance(result, dict):
                            raise ProtocolError("subscription failed")
                        if result.get("protocol") != "parano1d-stratum-v1" or result.get("nonce_bits") != 64:
                            raise ProtocolError("unsupported Stratum subscription")
                        namespace = hex_field(result.get("session_namespace"), 8)
                        self.send(connection, 2, "mining.authorize", [username, "x"])
                    elif request_id == 2 and not authorized:
                        if error or result is not True:
                            raise UnsafeShareError("pool authorization failed")
                        authorized = True
                        LOG.info("Pool authorized the worker")
                        if queued_job is not None:
                            self.publish(queued_job)
                            queued_job = None
                    elif method == "mining.notify":
                        if namespace is None:
                            raise ProtocolError("job arrived before subscription")
                        job = Job.parse(message.get("params"), namespace)
                        if authorized:
                            self.publish(job)
                        else:
                            queued_job = job
                    elif method == "mining.pause":
                        queued_job = None
                        self.invalidate()
                    elif method == "client.reconnect":
                        # Reopen the configured, authenticated host. Ignore redirect targets.
                        raise ConnectionError("pool requested reconnection")
                    elif type(request_id) is int and request_id in pending:
                        self.record_share(result is True, error, pending.pop(request_id))
                if len(buffer) >= MAX_LINE:
                    raise ProtocolError("oversize unfinished Stratum message")
                next_id = self.submit_candidates(connection, pending, next_id)
        finally:
            self.invalidate()


class UnsafeShareError(Exception):
    """Stop a prototype instead of repeatedly submitting invalid work."""


def run(args):
    tls = ssl.create_default_context()
    with subprocess.Popen(args.engine, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True, encoding="ascii", bufsize=1) as engine:
        miner = Miner(engine)
        delay = 1
        try:
            while not miner.failed.is_set():
                started = time.monotonic()
                try:
                    with socket.create_connection((args.host, args.port), timeout=15) as tcp:
                        with tls.wrap_socket(tcp, server_hostname=args.host) as connection:
                            miner.session(connection, f"{args.payout_address}.{args.worker}")
                except UnsafeShareError as exc:
                    LOG.error("Stopped the prototype: %s", exc)
                    return 1
                except (OSError, ProtocolError) as exc:
                    if miner.failed.is_set():
                        break
                    if args.once:
                        LOG.error("Pool session ended: %s", exc)
                        return 1
                    if time.monotonic() - started >= 60:
                        delay = 1
                    LOG.warning("Pool session ended: %s. Reconnect in %d s", exc, delay)
                    miner.failed.wait(delay)
                    delay = min(30, delay * 2)
        except KeyboardInterrupt:
            return 0
        finally:
            try:
                miner.engine_command("quit")
                engine.wait(timeout=3)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                if engine.poll() is None:
                    engine.kill()
                    engine.wait()
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Stratum TLS hostname")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--payout-address", required=True, help="public NOID receiving address")
    parser.add_argument("--worker", required=True)
    parser.add_argument("--once", action="store_true", help="exit after the first disconnected session")
    parser.add_argument("--engine", nargs=argparse.REMAINDER, required=True,
                        help="absolute path to the search engine and its arguments, placed last")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in the range 1 through 65535")
    if not PAYOUT_ADDRESS.fullmatch(args.payout_address) or not WORKER.fullmatch(args.worker):
        parser.error("a public o1 address and a worker of at most 32 letters, digits, underscores or hyphens are required")
    if not args.engine or not Path(args.engine[0]).is_absolute():
        parser.error("--engine requires an absolute executable path")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    LOG.info("Parano1d CPU Miner Prototype 0.10.0 — portable rig candidate")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
