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

PCLMUL static int compare_product(wide a, wide b)
{
    const vector av = from_wide(basis(a, flat_to_tower64));
    const vector bv = from_wide(basis(b, flat_to_tower64));
    return flat_result(multiply(av, bv)) == multiply_scalar(a, b);
}

PCLMUL static int run_checks(void)
{
    unsigned products = 0, squares = 0, fixed_products = 0;
    for (unsigned i = 0; i < 128; ++i)
        for (unsigned j = 0; j < 128; ++j) {
            if (!compare_product((wide)1 << i, (wide)1 << j)) {
                fprintf(stderr, "FAIL: field basis product %u x %u\n", i, j);
                return 1;
            }
            ++products;
        }
    for (unsigned trial = 0; trial < 2048; ++trial) {
        const wide a = (wide)next() | (wide)next() << 64;
        const wide b = (wide)next() | (wide)next() << 64;
        const vector av = from_wide(basis(a, flat_to_tower64));
        if (!compare_product(a, b)) {
            fprintf(stderr, "FAIL: dense field product %u\n", trial);
            return 1;
        }
        if (flat_result(square(av)) != multiply_scalar(a, a)) {
            fprintf(stderr, "FAIL: dense field square %u\n", trial);
            return 1;
        }
        ++products;
        ++squares;
    }
    for (unsigned i = 0; i < 128; ++i) {
        const wide a = (wide)1 << i;
        const vector av = from_wide(basis(a, flat_to_tower64));
        for (unsigned j = 0; j < 6; ++j) {
            const wide coefficient = j < 4
                ? load_constant(noid_mds_partial[5*j]) ^ 1
                : load_constant(noid_tower_to_flat[j-3]);
            const vector cv = j < 4 ? diagonal[j] : j == 4 ? two : four;
            if (flat_result(multiply_fixed(av, cv)) != multiply_scalar(a, coefficient)) {
                fprintf(stderr, "FAIL: fixed matrix product bit %u coefficient %u\n", i, j);
                return 1;
            }
            ++fixed_products;
        }
    }
    printf("PASS: %u independent field products, %u squares, %u fixed-coefficient products\n",
           products, squares, fixed_products);
    return 0;
}

int main(void)
{
    if (!noid_cpu_supported()) {
        fputs("SSSE3 and PCLMULQDQ are required for the accelerated field tests\n", stderr);
        return 1;
    }
    pthread_once(&basis_ready, init_basis);
    pthread_once(&backend_ready, init_backend);
    return run_checks();
}
