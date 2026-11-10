#!/usr/bin/env python3
"""Emit public v2.1.0 PoW tables and independent scalar test vectors."""
from pathlib import Path
import argparse
import functools
import random
import re

def array(text, name):
    match = re.search(rf"\b{name}\s*:\s*\[.*?=\s*\[(.*?)\];", text, re.S)
    if not match:
        raise ValueError(f"missing public constant {name}")
    return [int(token, 16) for token in re.findall(r"0x[0-9a-fA-F_]+", match[1])]

def apply_basis(columns, value):
    output = 0
    while value:
        bit = value & -value
        output ^= columns[bit.bit_length() - 1]
        value ^= bit
    return output

def gf_product(a, b):
    result = 0
    while b:
        bit = b & -b
        result ^= a << (bit.bit_length() - 1)
        b ^= bit
    modulus = (1 << 128) | 0x87
    while result.bit_length() > 128:
        result ^= modulus << (result.bit_length() - 129)
    return result

def xor_all(xs):
    return functools.reduce(int.__xor__, xs, 0)

def pair(value):
    return "{ UINT64_C(0x%016x), UINT64_C(0x%016x) }" % (value & ((1<<64)-1), value >> 64)

def byte_row(data):
    return "{ " + ", ".join(f"0x{x:02x}" for x in data) + " }"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('public_source', type=Path)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    source = args.public_source
    hardware = (source / 'noid_core/src/hardware.rs').read_text()
    permutation = (source / 'noid_poseidon2b/src/native/permutation.rs').read_text()
    genesis = (source / 'noid_chain/src/consensus/genesis.rs').read_text()
    t2f = array(hardware, "TOWER_TO_FLAT")
    f2t = array(hardware, "FLAT_TO_TOWER")
    rc = array(permutation, "ROUND_CONSTANTS")
    external = array(permutation, "MDS_FULL")
    internal = array(permutation, "MDS_PARTIAL")
    assert (len(t2f), len(f2t), len(rc), len(external), len(internal)) == (128, 128, 264, 16, 16)
    assert all(apply_basis(f2t, v) == 1 << i for i, v in enumerate(t2f))
    assert all(apply_basis(t2f, v) == 1 << i for i, v in enumerate(f2t))
    convert = functools.partial(apply_basis, t2f)
    converted_rc, converted_external, converted_internal = (
        [convert(x) for x in values] for values in (rc, external, internal)
    )
    tag = int.from_bytes(b"POWHDR__", "big")
    iv = [convert(tag << 64), convert(tag)]

    def matrix(state, elements):
        return [
            xor_all(gf_product(elements[4 * i + j], state[j]) for j in range(4))
            for i in range(4)
        ]

    def sbox(x):
        square = gf_product(x, x)
        fourth = gf_product(square, square)
        return gf_product(gf_product(x, square), fourth)

    def permute(state):
        state = matrix(state, converted_external)
        for i in range(66):
            if 4 <= i < 62:
                state[0] = sbox(state[0] ^ converted_rc[i])
                state = matrix(state, converted_internal)
            else:
                state = [sbox(value ^ converted_rc[66 * j + i]) for j, value in enumerate(state)]
                state = matrix(state, converted_external)
        return state

    def digest(fields):
        state = [0, 0, *iv]
        for offset in range(0, 256, 32):
            state[0] ^= convert(int.from_bytes(fields[offset:offset+16], "little"))
            state[1] ^= convert(int.from_bytes(fields[offset+16:offset+32], "little"))
            state = permute(state)
        return b"".join(apply_basis(f2t, value).to_bytes(16, "little") for value in state[:2])

    def words(values):
        return b"".join(value.to_bytes(16, "little") for value in values)

    genesis_state_root = bytes(array(genesis, "GENESIS_STATE_ROOT"))
    timestamp = int(re.search(r"GENESIS_TIMESTAMP: u64 = ([0-9_]+)", genesis)[1].replace("_", ""))
    nonce = int(re.search(r"GENESIS_NONCE: u128 = ([0-9_]+)", genesis)[1].replace("_", ""))
    target = 1 << 238
    genesis_fields = (
        bytes(32) + genesis_state_root + bytes(32) + words([timestamp, 0]) + bytes(32)
        + words([nonce]) + target.to_bytes(32, "little") + words([24, 0, 0])
    )
    genesis_digest = digest(genesis_fields)
    assert len(genesis_fields) == 256 and int.from_bytes(genesis_digest, "little") < target

    rng = random.Random(0x45B210)
    cases = [genesis_fields, bytes(256), bytes(range(256)), bytes([255]) * 256]
    for next_nonce in [0, 1, (1<<64)-1, 1<<64, (1<<127), (1<<128)-1]:
        data = bytearray(rng.randbytes(256))
        data[160:176] = next_nonce.to_bytes(16, "little")
        cases.append(bytes(data))
    vectors = [(data, digest(data)) for data in cases]

    def emit_array(name, values):
        return f"static const uint64_t {name}[{len(values)}][2] = {{\n    " + ",\n    ".join(map(pair, values)) + "\n};\n"

    notice = "// SPDX-License-Identifier: Apache-2.0\n// Consensus constants from Parano1d v2.1.0, Copyright (C) 2026 Paranoid Zero.\n"
    tables = notice + emit_array("noid_tower_to_flat", t2f) + emit_array("noid_flat_to_tower", f2t)
    tables += emit_array("noid_round", converted_rc)
    tables += emit_array("noid_mds_full", converted_external)
    tables += emit_array("noid_mds_partial", converted_internal)
    tables += emit_array("noid_iv", iv)
    def encode(value):
        reversed_bits = int(f"{value:0128b}"[::-1], 2)
        mask = (1 << 128) - 1
        r = 0xC2000000000000000000000000000001
        return ((reversed_bits << 1) & mask) ^ (r if value & 1 else 0)

    def decode(value):
        bit = value & 1
        r = 0xC2000000000000000000000000000001
        return int(f"{((value ^ (r if bit else 0)) >> 1):0128b}"[::-1], 2) | bit

    assert all(decode(encode(1 << i)) == 1 << i for i in range(128))
    assert all(encode(decode(1 << i)) == 1 << i for i in range(128))
    tables += emit_array("noid_flat_to_mont", [encode(1 << i) for i in range(128)])
    tables += emit_array("noid_tower_to_mont", [encode(x) for x in t2f])
    tables += emit_array("noid_mont_to_tower", [apply_basis(f2t, decode(1 << i)) for i in range(128)])
    tables += emit_array("noid_mont_round", [encode(x) for x in converted_rc])
    tables += emit_array("noid_mont_diag", [encode(converted_internal[5*i] ^ 1) for i in range(4)])
    (args.directory / 'noid_tables.h').write_text(tables)
    fixtures = "// Generated by generate.py from public Parano1d v2.1.0.\n"
    fixtures += "struct noid_fixture { uint8_t fields[256]; uint8_t digest[32]; };\n"
    fixtures += "static const struct noid_fixture noid_fixtures[] = {\n"
    for data, digest_value in vectors:
        fixtures += "    {\n        " + byte_row(data) + ",\n        " + byte_row(digest_value) + "\n    },\n"
    fixtures += "};\n"
    (args.directory / 'noid_fixtures.h').write_text(fixtures)
    print(f"genesis nonce: {nonce} | LE PoW digest: {genesis_digest.hex()}")
    print(f"generated {len(vectors)} independent vectors")

if __name__ == '__main__':
    main()
