#!/usr/bin/env python3
"""Generate the Poseidon2b AES-tower basis and fixed GFNI maps."""

import argparse
from pathlib import Path
import re

from generate import apply_basis, gf_product


def load_array(source, name):
    body = re.search(rf"\b{name}\[\d+\]\[2\]\s*=\s*\{{(.*?)\n\}};", source, re.S)
    if body is None:
        raise ValueError(f"missing field table {name}")
    words = [int(word, 16) for word in re.findall(r"UINT64_C\((0x[0-9a-f]+)\)", body[1])]
    if len(words) % 2:
        raise ValueError(f"unpaired field-table words in {name}")
    return [low | high << 64 for low, high in zip(words[::2], words[1::2])]


def linear_solver(columns):
    """Build a solver over F2. Non-pivot variables are assigned zero."""
    pivots = {}
    for index, column in enumerate(columns):
        coefficients = 1 << index
        while column:
            pivot = column.bit_length() - 1
            if pivot not in pivots:
                pivots[pivot] = column, coefficients
                break
            value, mask = pivots[pivot]
            column ^= value
            coefficients ^= mask

    def solve(value):
        coefficients = 0
        while value:
            pivot = value.bit_length() - 1
            if pivot not in pivots:
                raise ValueError("element outside the image of the linear map")
            column, mask = pivots[pivot]
            value ^= column
            coefficients ^= mask
        return coefficients

    return solve


def aes_product(a, b):
    result = 0
    while b:
        if b & 1:
            result ^= a
        a = (a << 1) ^ ((a >> 7) * 0x11B)
        b >>= 1
    return result


def powers(a, count, multiply):
    values = [1]
    for _ in range(1, count):
        values.append(multiply(values[-1], a))
    return values


def affine_map(function):
    """Pack the columns of a byte-linear map in GFNI's row-byte order."""
    rows = [0] * 8
    for column in range(8):
        result = function(1 << column)
        for row in range(8):
            rows[row] |= ((result >> row) & 1) << column
    return sum(row << (8 * (7 - index)) for index, row in enumerate(rows))


def array(name, values):
    pairs = [
        f"    {{ UINT64_C(0x{value & ((1 << 64) - 1):016x}), UINT64_C(0x{value >> 64:016x}) }},"
        for value in values
    ]
    return "\n".join([f"static const uint64_t {name}[128][2] = {{", *pairs, "};", ""])


def generate(source):
    tower_to_flat = load_array(source, "noid_tower_to_flat")
    flat_to_tower = load_array(source, "noid_flat_to_tower")
    diagonal = load_array(source, "noid_mds_partial")

    # The canonical 0x10 element maps to AES polynomial byte 0x02.
    # Express the AES coordinate basis in the reference polynomial field.
    canonical_powers = powers(tower_to_flat[4], 8, gf_product)
    aes_powers = powers(0x02, 8, aes_product)
    aes_to_powers = linear_solver(aes_powers)
    aes_to_flat = [apply_basis(canonical_powers, aes_to_powers(1 << i)) for i in range(8)]
    for i, a in enumerate(aes_to_flat):
        for j, b in enumerate(aes_to_flat):
            if gf_product(a, b) != apply_basis(aes_to_flat, aes_product(1 << i, 1 << j)):
                raise ValueError("AES byte embedding disagrees with the reference field")

    # The canonical y has trace one and norm mu. The norm of
    # the next tower root is 1+y/mu and has relative norm one.
    root = tower_to_flat[8]
    root_norm = gf_product(root, root) ^ root
    norm_byte = linear_solver(aes_to_flat)(root_norm)
    alpha_byte = next(value for value in range(1, 256)
                      if aes_product(value, norm_byte) == 1)
    alpha_flat = apply_basis(aes_to_flat, alpha_byte)
    aes_to_flat = [gf_product(factor, element)
                   for factor in (root, root ^ 1) for element in aes_to_flat]

    # Trace-one higher tower levels preserve the conjugate 16-bit basis.
    monomials = [1 << i for i in range(128)]
    monomial_squares = [gf_product(element, element) for element in monomials]
    artin_schreier = linear_solver([
        square ^ element for square, element in zip(monomial_squares, monomials)
    ])
    root16 = gf_product(root, alpha_flat) ^ 1
    inv16 = root16 ^ alpha_flat
    root32 = artin_schreier(root16)
    aes_to_flat += [gf_product(root32, element) for element in aes_to_flat]
    root64 = artin_schreier(gf_product(root32, inv16))
    aes_to_flat += [gf_product(root64, element) for element in aes_to_flat]
    top_norm = gf_product(root64, gf_product(root32 ^ 1, inv16))
    top_root = artin_schreier(top_norm)
    aes_to_flat += [gf_product(top_root, element) for element in aes_to_flat]

    flat_to_aes = linear_solver(aes_to_flat)
    flat_columns = [flat_to_aes(element) for element in monomials]
    tower_columns = [flat_to_aes(element) for element in tower_to_flat]
    inverse_columns = [apply_basis(flat_to_tower, element) for element in aes_to_flat]
    for i in range(128):
        if apply_basis(inverse_columns, tower_columns[i]) != (1 << i):
            raise ValueError("generated field bases do not round trip")

    high_byte_maps = [
        affine_map(lambda value, byte=byte: apply_basis(inverse_columns, value << (8 * byte)) >> 120)
        for byte in range(16)
    ]
    if any(high_byte_maps[:14]) or high_byte_maps[14] != high_byte_maps[15]:
        raise ValueError("high-byte trace projection changed; update scan projection")

    diag = [flat_to_aes(diagonal[5 * i] ^ 1) for i in range(4)]
    if diag[0] >> 8 != diag[0] & 255 or any(value >> 16 or value >> 8 != 1 for value in diag[1:]):
        raise ValueError("unexpected diagonal coefficients in conjugate basis")

    maps = {
        "NOID_AES_HIGH_BYTE_MAP": high_byte_maps[14],
        "NOID_AES_MUL_ALPHA": affine_map(lambda a: aes_product(a, alpha_byte)),
        "NOID_AES_SQUARE": affine_map(lambda a: aes_product(a, a)),
        "NOID_AES_SQUARE_ALPHA": affine_map(lambda a: aes_product(aes_product(a, a), norm_byte)),
    }
    byte_multipliers = {
        "NOID_AES_MUL_TWO": flat_to_aes(tower_to_flat[1]) & 255,
        "NOID_AES_MUL_FOUR": flat_to_aes(tower_to_flat[2]) & 255,
        "NOID_AES_DIAG_0": diag[0] & 255,
        "NOID_AES_NORM16": norm_byte,
    }
    for i, value in enumerate(diag[1:], 1):
        high = (value & 255) ^ 1
        byte_multipliers[f"NOID_AES_DIAG_{i}_HIGH"] = aes_product(high, norm_byte)
        byte_multipliers[f"NOID_AES_DIAG_{i}_SCALED"] = 1 ^ high
    maps.update({
        name: affine_map(lambda value, multiplier=multiplier: aes_product(value, multiplier))
        for name, multiplier in byte_multipliers.items()
    })

    lines = [
        "/* SPDX-License-Identifier: Apache-2.0 */",
        "/* Generated by generate_aes_tables.py from noid_tables.h. */",
        "#ifndef NOID_AES_TABLES_H",
        "#define NOID_AES_TABLES_H",
        "",
        "#include <stdint.h>",
        "",
        array("f2new", flat_columns),
        array("t2new", tower_columns),
        array("new2t", inverse_columns),
    ]
    lines.extend(f"#define {name} UINT64_C(0x{matrix:016x})" for name, matrix in maps.items())
    lines.extend(["", "#endif", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    directory = Path(__file__).resolve().parent
    parser.add_argument("--input", type=Path, default=directory / "noid_tables.h")
    parser.add_argument("--output", type=Path, default=directory / "noid_aes_tables.h")
    args = parser.parse_args()
    args.output.write_text(generate(args.input.read_text()))


if __name__ == "__main__":
    main()
