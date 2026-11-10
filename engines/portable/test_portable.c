/* SPDX-License-Identifier: Apache-2.0
 * CPU HyperNOID Miner v1.0.0 portable-backend arithmetic checks.
 * The optimized nibble path is checked against the independent bit-serial
 * polynomial reference. Public consensus vectors run separately.
 */
#include <stdio.h>
#include "noid.c"

static uint64_t random_word(uint64_t *state)
{
    *state ^= *state << 13;
    *state ^= *state >> 7;
    *state ^= *state << 17;
    return *state;
}

int main(void)
{
    uint64_t seed = UINT64_C(0x68797065726e6f69);
    pthread_once(&basis_ready, init_basis);
    pthread_once(&products_ready, init_products);
    for (unsigned i = 0; i < 4096; ++i) {
        wide a = (wide)random_word(&seed) << 64;
        a |= random_word(&seed);
        wide b = (wide)random_word(&seed) << 64;
        b |= random_word(&seed);
        if (multiply_portable(a, b) != multiply_scalar(a, b)) {
            fprintf(stderr, "field product mismatch at case %u\n", i);
            return 1;
        }
        if (multiply_portable(a, a) != multiply_scalar(a, a)) {
            fprintf(stderr, "field square mismatch at case %u\n", i);
            return 1;
        }
        if (seventh_portable(a) != scalar_seventh(a)) {
            fprintf(stderr, "seventh power mismatch at case %u\n", i);
            return 1;
        }
        uint8_t bytes[16];
        write_le(bytes, a);
        if (read_le(bytes) != a) return 1;
        for (unsigned byte = 0; byte < 16; ++byte)
            if (bytes[byte] != (uint8_t)(a >> (8 * byte))) return 1;
    }
    for (unsigned bit = 0; bit < 128; ++bit) {
        wide a = (wide)1 << bit;
        if (basis(basis(a, to_flat), to_tower) != a) return 1;
        for (unsigned coefficient = 0; coefficient < 16; ++coefficient) {
            if (basis(a, full_products[coefficient]) !=
                multiply_scalar(a, load_constant(noid_mds_full[coefficient]))) return 1;
            if (basis(a, partial_products[coefficient]) !=
                multiply_scalar(a, load_constant(noid_mds_partial[coefficient]))) return 1;
        }
    }
    for (unsigned i = 0; i < 16; ++i) {
        wide a[4], b[4];
        for (unsigned lane = 0; lane < 4; ++lane) {
            a[lane] = (wide)random_word(&seed) << 64;
            a[lane] |= random_word(&seed);
            b[lane] = a[lane];
        }
        permute_scalar(a);
        permute_portable(b);
        if (memcmp(a, b, sizeof(a)) != 0) return 1;
    }
    puts("PASS: 4096 products, squares, seventh powers and byte-order checks; 128 basis vectors and 4096 fixed products; 16 complete permutations");
    return 0;
}
