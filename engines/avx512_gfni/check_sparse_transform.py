#!/usr/bin/env python3
"""Reproduce and exactly verify Prototype 9's sparse linear transformation.

The oracle uses the original noid_tables.h matrices and flat-field
multiplication from generate.py. The implementation is separately probed
through freshly generated assembly and the actual static inverse function.
No mining or network connections are performed, and source files are not
modified. A C compiler and the miner's AVX-512/GFNI CPU support are required.
"""
from functools import lru_cache
import ast
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from generate import gf_product

ROOT = Path(__file__).resolve().parent
IDENTITY = 0x0101


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def array(source, name):
    match = re.search(rf"\b{re.escape(name)}\[\d+\]\[2\]\s*=\s*\{{(.*?)\n\}};", source, re.S)
    require(match is not None, f"missing reference array {name}")
    numbers = [int(x, 16) for x in re.findall(r"UINT64_C\((0x[0-9a-fA-F]+)\)", match[1])]
    require(len(numbers) % 2 == 0, f"incomplete reference array {name}")
    return [a | b << 64 for a, b in zip(numbers[::2], numbers[1::2])]


def apply(columns, value):
    result = 0
    while value:
        bit = value & -value
        result ^= columns[bit.bit_length() - 1]
        value ^= bit
    return result


reference = (ROOT / "noid_tables.h").read_text()
header = (ROOT / "noid_aes_tables.h").read_text()
source = (ROOT / "noid.c").read_text()
generator = (ROOT / "generate_seventh.py").read_text()
flat_to_aes = array(header, "f2new")
aes_to_tower = array(header, "new2t")
tower_to_flat = array(reference, "noid_tower_to_flat")
aes_to_flat = [apply(tower_to_flat, x) for x in aes_to_tower]
require(apply(aes_to_flat, IDENTITY) == 1, "unexpected conjugate field identity")
for bit in range(128):
    require(apply(flat_to_aes, aes_to_flat[bit]) == 1 << bit, "field bases are not inverse")


@lru_cache(maxsize=None)
def multiply(a, b):
    """GF(2^16) multiplication independently via the original GF(2^128)."""
    value = apply(flat_to_aes, gf_product(apply(aes_to_flat, a), apply(aes_to_flat, b)))
    require(value < 65536, "coefficient escaped GF(2^16)")
    return value


def power(a, exponent):
    result = IDENTITY
    while exponent:
        if exponent & 1:
            result = multiply(result, a)
        a = multiply(a, a)
        exponent >>= 1
    return result


def inverse(a):
    require(a != 0, "singular coefficient")
    result = power(a, 65534)
    require(multiply(a, result) == IDENTITY, "field inverse failed")
    return result


def xor_all(values):
    result = 0
    for x in values:
        result ^= x
    return result


def matmul(a, b):
    return [[xor_all(multiply(x, y) for x, y in zip(row, column)) for column in zip(*b)] for row in a]


def mat_inverse(matrix):
    n = len(matrix)
    rows = [list(row) + [IDENTITY if i == j else 0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = next((row for row in range(col, n) if rows[row][col]), None)
        require(pivot is not None, "singular transformation matrix")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = inverse(rows[col][col])
        rows[col] = [multiply(x, scale) for x in rows[col]]
        for row in range(n):
            if row == col:
                continue
            scale = rows[row][col]
            rows[row] = [x ^ multiply(scale, y) for x, y in zip(rows[row], rows[col])]
    return [row[n:] for row in rows]


def converted_matrix(name):
    values = [apply(flat_to_aes, x) for x in array(reference, name)]
    require(all(x < 65536 for x in values), f"{name} coefficients escaped GF(2^16)")
    return [values[4 * row:4 * row + 4] for row in range(4)]


partial = converted_matrix("noid_mds_partial")
external = converted_matrix("noid_mds_full")
A = [row[1:] for row in partial[1:]]
w = [IDENTITY] * 3
K = [w, matmul([w], A)[0], matmul(matmul([w], A), A)[0]]
companion = matmul(matmul(K, A), mat_inverse(K))
require(companion[:2] == [[0, IDENTITY, 0], [0, 0, IDENTITY]], "unexpected Krylov companion form")
c0, c1, c2 = companion[2]
alpha = c1 ^ IDENTITY
k = c0 ^ multiply(c2, c1)
gamma = multiply(k, inverse(alpha))
T0 = [w, [x ^ multiply(c2, y) for x, y in zip(K[1], K[0])],
      [x ^ multiply(c2, y) for x, y in zip(K[2], K[1])]]
T = [T0[0]] + [[multiply(x, inverse(alpha)) for x in row] for row in T0[1:]]
Ti = mat_inverse(T)
require((c2, alpha, gamma, c1) == (0x009D, 0x42AB, 0x0029, 0x43AA), "derived sparse constants changed")
expected_A = [[c2, alpha, 0], [0, 0, IDENTITY], [gamma, c1, 0]]
require(matmul(matmul(T, A), Ti) == expected_A, "sparse similarity transform failed")
require(matmul(T, [[IDENTITY]] * 3) == [[IDENTITY], [0], [0]], "S-box input coupling changed")
require(T[0] == w, "nonlinear-word output coupling changed")
P = [[IDENTITY, 0, 0, 0]] + [[0] + row for row in T]
Pi = mat_inverse(P)
expected_full = [[partial[0][0], IDENTITY, 0, 0], [IDENTITY, c2, alpha, 0],
                 [0, 0, 0, IDENTITY], [0, gamma, c1, 0]]
require(matmul(matmul(P, partial), Pi) == expected_full, "complete partial-round conjugacy failed")

# Resolve the implementation's declared entry matrix as data, without
# importing its executable generator or trusting its derivation.
entry = next(node for node in ast.parse(generator).body
             if isinstance(node, ast.FunctionDef) and node.name == "external_sparse4_assembly")
assignments = {node.targets[0].id: ast.literal_eval(node.value)
               for node in entry.body if isinstance(node, ast.Assign)
               and isinstance(node.targets[0], ast.Name)
               and node.targets[0].id in ("transform", "coeffs")}
require(assignments["transform"] == T, "hardcoded entry T differs from independent derivation")
require(all(Ti[row][2] >> 8 == (Ti[row][2] & 255) == (Ti[row][1] >> 8) for row in range(3)),
        "optimized inverse coefficient identity changed")
require(all(xor_all(Ti[row][col] for row in range(3)) == (IDENTITY if col == 0 else 0)
            for col in range(3)), "inverse output-sum identity failed")

# Validate the fused entry formula directly against the original full matrix.
two = apply(flat_to_aes, tower_to_flat[1])
four = apply(flat_to_aes, tower_to_flat[2])
five = four ^ IDENTITY
D1 = [five, five ^ two, five, five ^ two]
D2 = [0, two, IDENTITY, IDENTITY]
fused = [[D1[i] ^ (four if i >= 2 else 0) for i in range(4)], [four, four, 0, 0]]
fused += [[multiply(row[0], x) ^ multiply(row[1], y) for x, y in zip(D1, D2)] for row in T[1:]]
require(fused == matmul(P, external), "fused external-entry formula differs from original matrix")
print("PASS: sparse T, inverse, input/output coupling, scaled constants, and fused matrix are exactly equivalent")

# GFNI maps are checked using original flat-field multiplication, rather than
# the generator's AES arithmetic or affine-map construction routines.
macros = dict((name, int(value, 16)) for name, value in re.findall(
    r"#define\s+(NOID_[A-Z0-9_]+)\s+UINT64_C\((0x[0-9a-fA-F]+)\)", header))


def c_table(name):
    match = re.search(rf"\b{re.escape(name)}\[\]\s*=\s*\{{(.*?)\}};", source, re.S)
    require(match is not None, f"missing implementation map table {name}")
    body = re.sub(r"/\*.*?\*/", "", match[1], flags=re.S)
    result = []
    for token in body.split(","):
        token = token.strip()
        if not token:
            continue
        if token in macros:
            result.append(macros[token])
        else:
            value = re.fullmatch(r"UINT64_C\((0x[0-9a-fA-F]+)\)", token)
            require(value is not None, f"unsupported map-table token {token}")
            result.append(int(value[1], 16))
    return result


def gfni(matrix, byte):
    return sum((((matrix >> (8 * (7 - bit))) & 255) & byte).bit_count() % 2 << bit for bit in range(8))


def scalar(value):
    return value * IDENTITY


def byte_product(a, b):
    result = multiply(scalar(a), scalar(b))
    require(result >> 8 == (result & 255), "GF(2^8) embedding changed")
    return result & 255


root = tower_to_flat[8]
norm_aes = apply(flat_to_aes, gf_product(root, root) ^ root)
require(norm_aes >> 8 == (norm_aes & 255), "quadratic norm is outside GF(2^8)")
norm = norm_aes & 255
diag = [partial[j][j] ^ IDENTITY for j in range(4)]
first = [diag[0] & 255]
for value in diag[1:]:
    require(value >> 8 == 1, "unexpected original partial coefficient")
    first.extend([byte_product((value & 255) ^ (value >> 8), norm), value & 255])
first.append(norm)
cmix = byte_product((c1 & 255) ^ (c1 >> 8), norm)
first.extend([c2 & 255, byte_product((c2 & 255) ^ (c2 >> 8), norm),
              gamma & 255, byte_product((gamma & 255) ^ (gamma >> 8), norm),
              c1 & 255, c1 >> 8, cmix, (c1 & 255) ^ cmix])
coeffs = assignments["coeffs"]
require(coeffs[:16] == first, "declared sparse map coefficients differ from independently derived values")
matrices = c_table("noid_internal_matrix")
require(len(matrices) == len(coeffs), "constant-map count differs from generator declaration")
for index, (matrix, coefficient) in enumerate(zip(matrices, coeffs)):
    for bit in range(8):
        value = 1 << bit
        require(gfni(matrix, value) == byte_product(coefficient, value), f"incorrect internal GFNI map {index}, bit {bit}")
sbox = c_table("noid_sbox_matrix")
require(len(sbox) == 3, "unexpected S-box map table")
alpha_byte = inverse(norm_aes) & 255
for bit in range(8):
    value = 1 << bit
    square = byte_product(value, value)
    expected = [byte_product(alpha_byte, value), byte_product(norm, square), square]
    require([gfni(m, value) for m in sbox] == expected, f"incorrect S-box GFNI map, bit {bit}")
print(f"PASS: all {len(matrices) + len(sbox)} hardcoded GFNI maps match independent field arithmetic on every byte basis vector")

# Probe the actual implementation on every basis vector. The temporary C
# translation unit includes noid.c only to call its static inverse function;
# expected outputs are computed above from independent reference matrices.
PROBE = r'''
#include <stdio.h>
#include <string.h>
#include "noid.c"
AVX512 static int probe(int leave) {
    _Alignas(64) vector state[4][16];
    size_t count;
    while ((count=fread(state,1,sizeof(state),stdin)) != 0) {
        if (count != sizeof(state)) return 3;
        if (leave) partial_state_leave(state);
        else noid_external_sparse4_asm(state[0]);
        if (fwrite(state,1,sizeof(state),stdout) != sizeof(state)) return 4;
    }
    return ferror(stdin) ? 5 : 0;
}
int main(int argc,char **argv) {
    if (!noid_avx512_supported()) return 2;
    return probe(argc==2 && strcmp(argv[1],"leave")==0);
}
'''
cases = [(0, 0, 0, 0)]
for word in range(4):
    for bit in range(128):
        case = [0, 0, 0, 0]
        case[word] = 1 << bit
        cases.append(tuple(case))
padded = cases + [(0, 0, 0, 0)] * (-len(cases) % 64)
wire = bytearray()
for start in range(0, len(padded), 64):
    batch = padded[start:start + 64]
    for word in range(4):
        for byte in range(16):
            wire.extend((case[word] >> (8 * byte)) & 255 for case in batch)


def expected_case(matrix, case, key=0):
    flat_case = [apply(aes_to_flat, x) for x in case]
    values = [xor_all(gf_product(apply(aes_to_flat, a), b) for a, b in zip(row, flat_case)) for row in matrix]
    values[0] ^= key
    return [apply(flat_to_aes, value) for value in values]


round4_key = array(reference, "noid_round")[4]
with tempfile.TemporaryDirectory(prefix="noid-sparse-proof-") as temp_name:
    temp = Path(temp_name)
    assembly = temp / "noid-seventh.S"
    with assembly.open("wb") as output:
        subprocess.run([sys.executable, str(ROOT / "generate_seventh.py")], cwd=ROOT,
                       stdout=output, check=True)
    (temp / "probe.c").write_text(PROBE)
    compiler = shlex.split(os.environ.get("CC", "cc"))
    subprocess.run([*compiler, "-O2", "-std=c11", "-pthread", "-I", str(ROOT),
                    str(temp / "probe.c"), str(assembly), "-o", str(temp / "probe")], check=True)
    for mode, matrix, key in [("entry", fused, round4_key), ("leave", Pi, 0)]:
        result = subprocess.run([str(temp / "probe"), mode], input=wire, capture_output=True)
        require(result.returncode != 2, "AVX-512F/BW/GFNI support is needed for the executable basis-vector probe")
        require(result.returncode == 0, f"implementation probe failed ({result.returncode}): {result.stderr.decode(errors='replace')}")
        require(len(result.stdout) == len(wire), "implementation returned an incomplete basis-vector probe")
        for index, case in enumerate(cases):
            start = (index // 64) * 4096
            lane = index % 64
            got = [sum(result.stdout[start + (word * 16 + byte) * 64 + lane] << (8 * byte)
                       for byte in range(16)) for word in range(4)]
            require(got == expected_case(matrix, case, key), f"{mode} differs on basis case {index}")
        print(f"PASS: actual {mode} implementation matches reference on all 512 state basis vectors plus zero")
print("PASS: exact sparse-transformation reproducibility and equivalence checks complete")
