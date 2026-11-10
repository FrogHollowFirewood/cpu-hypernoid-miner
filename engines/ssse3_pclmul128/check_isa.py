#!/usr/bin/env python3
"""Reject accidental AVX/GFNI instructions in the compatibility executable."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

binary = Path(sys.argv[1] if len(sys.argv) > 1 else 'noid-worker').resolve(strict=True)
dump = subprocess.check_output(['objdump', '-d', '--no-show-raw-insn', str(binary)], text=True)
counts = Counter()
for line in dump.splitlines():
    match = re.match(r'^\s*[0-9a-f]+:\s+([a-zA-Z0-9_.]+)', line)
    if match:
        counts[match.group(1)] += 1
forbidden = {name: count for name, count in counts.items()
             if name.startswith('v') or 'gf2p8' in name}
if forbidden:
    raise SystemExit('FAIL: compatibility executable contains unsupported vector instructions: '
                     + json.dumps(forbidden, sort_keys=True))
if not any('pclmul' in name for name in counts) or not counts['pshufb']:
    raise SystemExit('FAIL: expected accelerated legacy PCLMUL and SSSE3 instructions were not found')
print('PASS: exact executable contains legacy PCLMUL and SSSE3, with no AVX/VEX or GFNI instructions')
print('SHA256: '+hashlib.sha256(binary.read_bytes()).hexdigest())
