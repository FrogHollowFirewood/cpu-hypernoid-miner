#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded native release validation: offline hashes and loopback TLS only."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build/validation"


def clean_commit():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT, check=True)
    other = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    if other:
        raise RuntimeError("Untracked source files exist; qualification requires a reviewed, clean Git tree")
    return commit


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ci-validation.json").unlink(missing_ok=True)
    source_sha = clean_commit()
    arch = platform.machine()
    capabilities = json.loads(subprocess.check_output([str(ROOT / "build/cpu-probe")], text=True))
    env = dict(os.environ, NOID_TEST_WORKER=str(ROOT / "engines/portable/noid-worker"),
               NONCE_TEST_TIMEOUT="300", PYTHONDONTWRITEBYTECODE="1")
    commands = [
        ("probe", [sys.executable, "engines/build_public.py", "check-probe", "--arch", arch]),
        ("engines", [sys.executable, "engines/build_public.py", "check-supported", "--arch", arch]),
        ("launcher", [sys.executable, "-B", "-m", "unittest", "discover", "-s", "launcher/tests", "-v"]),
        ("public-check", [str(ROOT / "hypernoid-miner"), "--check"]),
        ("public-self-test", [str(ROOT / "hypernoid-miner"), "--self-test"]),
    ]
    phases = []
    for name, command in commands:
        print("VALIDATING: " + name, flush=True)
        start = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=1200)
        # Test output is useful evidence, but the public artifact need not expose
        # local build roots or ephemeral temporary-directory names.
        sanitized = result.stdout.replace(str(ROOT), "<source-root>")
        (OUT / (name + ".log")).write_text(sanitized)
        print(sanitized, end="", flush=True)
        phases.append({"name": name, "exit_code": result.returncode,
                       "elapsed_seconds": round(time.monotonic() - start, 3)})
        if result.returncode:
            raise RuntimeError("Validation failed: " + name)
    manifest_data = (ROOT / "build/runtime-manifest.json").read_bytes()
    if clean_commit() != source_sha:
        raise RuntimeError("Source commit changed during qualification")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    evidence = {"schema": 1, "product": "CPU HyperNOID Miner", "version": "1.0.0",
                "architecture": arch, "source_commit": source_sha,
                "run_url": f"{server}/{repo}/actions/runs/{run}" if repo and run else None,
                "native": True, "backend_suites_passed": capabilities["supported_backends"],
                "phases": phases, "network_scope": "Loopback TLS test server only; no real pool mining.",
                "runtime_manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
                "scope": "Correctness and packaging qualification, not a hashrate or stability guarantee."}
    (OUT / "ci-validation.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print("PASS: all native release validation phases", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc))
