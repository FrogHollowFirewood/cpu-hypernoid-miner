/* SPDX-License-Identifier: Apache-2.0 */
/* Check the accelerated tower against the independent flat-field oracle. */
#include "noid.c"
#include <stdio.h>

static uint64_t random_state = UINT64_C(0x4a61b3ff7921750d);
static uint64_t next(void)
{
    random_state ^= random_state << 13;
    random_state ^= random_state >> 7;
    random_state ^= random_state << 17;
    return random_state;
}

PCLMUL static wide flat_result(vector encoded)
{
    return basis(basis(to_wide(encoded), from_tower64), to_flat);
}

PCLMUL static tower_pair encode_pair(wide a, wide b)
{
    return pack(from_wide(basis(a, flat_to_tower64)),
                from_wide(basis(b, flat_to_tower64)));
}

PCLMUL static int compare_pair(tower_pair actual, wide expected0, wide expected1)
{
    return flat_result(_mm256_castsi256_si128(actual)) == expected0
        && flat_result(_mm256_extracti128_si256(actual, 1)) == expected1;
}

PCLMUL static int compare_product(wide a0, wide b0, wide a1, wide b1)
{
    return compare_pair(multiply(encode_pair(a0, a1), encode_pair(b0, b1)),
                        multiply_scalar(a0, b0), multiply_scalar(a1, b1));
}

PCLMUL static int run_checks(void)
{
    unsigned products = 0, squares = 0, fixed_products = 0;
    for (unsigned i = 0; i < 128; ++i)
        for (unsigned j = 0; j < 128; ++j) {
            /* Both lanes exhaust all basis products, in different orders. */
            if (!compare_product((wide)1 << i, (wide)1 << j,
                                 (wide)1 << ((i + 37) % 128),
                                 (wide)1 << ((j + 71) % 128))) {
                fprintf(stderr, "FAIL: dual-lane field basis product %u x %u\n", i, j);
                return 1;
            }
            products += 2;
        }
    for (unsigned i = 0; i < 128; ++i) {
        const wide a0 = (wide)1 << i, a1 = (wide)1 << ((i + 37) % 128);
        if (!compare_pair(square(encode_pair(a0, a1)),
                          multiply_scalar(a0, a0), multiply_scalar(a1, a1))) {
            fprintf(stderr, "FAIL: dual-lane basis square %u\n", i);
            return 1;
        }
        squares += 2;
    }
    for (unsigned trial = 0; trial < 2048; ++trial) {
        wide a0 = (wide)next() | (wide)next() << 64;
        wide b0 = (wide)next() | (wide)next() << 64;
        wide a1 = (wide)next() | (wide)next() << 64;
        wide b1 = (wide)next() | (wide)next() << 64;
        /* Explicitly exercise a zero lane beside a nonzero lane and all ones. */
        if (trial == 0) a0 = b0 = 0;
        if (trial == 1) a1 = b1 = 0;
        if (trial == 2) a0 = b1 = ~(wide)0;
        if (!compare_product(a0, b0, a1, b1)) {
            fprintf(stderr, "FAIL: independent dual-lane field product %u\n", trial);
            return 1;
        }
        if (!compare_pair(square(encode_pair(a0, a1)),
                          multiply_scalar(a0, a0), multiply_scalar(a1, a1))) {
            fprintf(stderr, "FAIL: independent dual-lane field square %u\n", trial);
            return 1;
        }
        products += 2;
        squares += 2;
    }
    for (unsigned i = 0; i < 128; ++i) {
        const wide a0 = (wide)1 << i, a1 = (wide)1 << ((i + 37) % 128);
        const tower_pair av = encode_pair(a0, a1);
        for (unsigned j = 0; j < 6; ++j) {
            const wide coefficient = j < 4
                ? load_constant(noid_mds_partial[5*j]) ^ 1
                : load_constant(noid_tower_to_flat[j-3]);
            const vector cv = j < 4 ? diagonal[j] : j == 4 ? two : four;
            if (!compare_pair(multiply_fixed(av, cv), multiply_scalar(a0, coefficient),
                              multiply_scalar(a1, coefficient))) {
                fprintf(stderr, "FAIL: dual-lane fixed matrix product bit %u coefficient %u\n", i, j);
                return 1;
            }
            fixed_products += 2;
        }
    }
    printf("PASS: %u independent field products, %u squares, %u fixed-coefficient products\n",
           products, squares, fixed_products);
    return 0;
}

int main(void)
{
    if (!noid_cpu_supported()) {
        fputs("AVX2 and PCLMULQDQ are required for the accelerated field tests\n", stderr);
        return 1;
    }
    pthread_once(&basis_ready, init_basis);
    pthread_once(&backend_ready, init_backend);
    return run_checks();
}
