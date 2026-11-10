"""Check the SIMD worker against public Suprnova consensus vectors."""

import json
from pathlib import Path
import subprocess


root = Path(__file__).resolve().parent
vectors = json.loads((root / "consensus-vectors.json").read_text())
for vector in vectors:
    got = subprocess.check_output(
        [str(root / "noid-worker"), "--digest", vector["fields"]], text=True
    ).strip()
    if got != vector["digest"]:
        raise SystemExit(f"FAIL: {vector['name']} expected {vector['digest']}, got {got}")
    if "target" in vector:
        digest = int.from_bytes(bytes.fromhex(got), "little")
        target = int.from_bytes(bytes.fromhex(vector["target"]), "little")
        if (digest < target) != vector["meets_target"]:
            raise SystemExit(f"FAIL: {vector['name']} target comparison")
print(f"PASS: {len(vectors)} public PoW vectors, including mainnet headers")
