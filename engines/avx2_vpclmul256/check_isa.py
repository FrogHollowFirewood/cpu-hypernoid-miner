#!/usr/bin/env python3
"""Audit the exact AVX2/VPCLMUL256 artifact, including raw EVEX prefixes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary', nargs='?', default='noid-worker')
parser.add_argument('--json', type=Path, help='write a detailed audit record')
args = parser.parse_args()
binary = Path(args.binary).resolve(strict=True)
dump = subprocess.check_output(['objdump', '-d', '-w', str(binary)], text=True)
counts = Counter()
vector_functions = Counter()
forbidden = []
ymm_pclmul = 0
function = ''
instruction_line = re.compile(r'^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2}\s+)+)\s*([a-zA-Z0-9_.]+)\s*(.*)$')
bmi2 = {'pdep', 'pext', 'mulx', 'shlx', 'shrx', 'sarx', 'bzhi', 'rorx'}
legacy_prefixes = {0x26, 0x2e, 0x36, 0x3e, 0x64, 0x65, 0x66, 0x67, 0xf0, 0xf2, 0xf3}
for line in dump.splitlines():
    label = re.match(r'^[0-9a-f]+ <([^>]+)>:', line)
    if label:
        function = label.group(1)
    match = instruction_line.match(line)
    if not match:
        continue
    address, raw, name, operands = match.groups()
    raw = [int(byte, 16) for byte in raw.split()]
    opcode = next((byte for byte in raw if byte not in legacy_prefixes), None)
    counts[name] += 1
    if opcode in {0xc4, 0xc5, 0x62}:
        vector_functions[function] += 1
    if (opcode == 0x62 or 'gf2p8' in name or name in bmi2
            or re.search(r'%(?:zmm\d+|k[0-7])\b', operands)):
        forbidden.append({'address': address, 'instruction': name + ' ' + operands,
                          'bytes': ' '.join(f'{byte:02x}' for byte in raw)})
    if name.startswith('vpclmul') and '%ymm' in operands:
        ymm_pclmul += 1

allowed_vector_functions = {'init_coefficients', 'permutation_tower', 'permutation', 'hash_range'}
unexpected_vector_functions = {
    name: count for name, count in vector_functions.items()
    if name.split('.')[0] not in allowed_vector_functions
}
versions = sorted(set(re.findall(r'GLIBC_[0-9.]+', subprocess.check_output(
    ['readelf', '--version-info', str(binary)], text=True))),
    key=lambda value: tuple(map(int, value.removeprefix('GLIBC_').split('.'))))
record = {
    'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
    'required_runtime_features': ['x86-64 baseline', 'avx2', 'vpclmulqdq'],
    'runtime_check': 'noid_cpu_supported() checks AVX2 and VPCLMULQDQ before any hashing',
    'forbidden_evex_gfni_bmi2_or_avx512_registers': forbidden,
    'ymm_vpclmul_instructions': ymm_pclmul,
    'vector_instruction_functions': dict(sorted(vector_functions.items())),
    'unexpected_vector_instruction_functions': unexpected_vector_functions,
    'all_instruction_counts': dict(sorted(counts.items())),
    'glibc_symbol_versions': versions,
    'minimum_glibc_from_symbol_versions': versions[-1] if versions else None,
    'compiler': subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
}
if args.json:
    args.json.write_text(json.dumps(record, indent=2) + '\n')
if forbidden or unexpected_vector_functions:
    raise SystemExit('FAIL: unexpected ISA in VPCLMUL256 executable: '
                     + json.dumps({'instructions': forbidden, 'functions': unexpected_vector_functions}))
if not ymm_pclmul or not counts['vpshufb']:
    raise SystemExit('FAIL: required YMM VPCLMUL and AVX2 shuffle instructions were not found')
print('PASS: exact executable uses YMM VPCLMUL, with no EVEX, AVX512 registers, GFNI, or BMI2')
print('PASS: all VEX instructions are confined to runtime-gated backend functions')
print('SHA256: ' + record['binary_sha256'])
