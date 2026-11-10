/* Inspect the candidate's private CLMUL helper against its scalar oracle. */
#ifndef NOID_TEST_IMPLEMENTATION
#define NOID_TEST_IMPLEMENTATION "noid.c"
#endif
#include NOID_TEST_IMPLEMENTATION
#include <stdio.h>

int main(void)
{
    unsigned checks = 0;
    for (unsigned i = 0; i < 128; ++i) {
        for (unsigned j = 0; j < 128; ++j) {
            const wide a = (wide)1 << i, b = (wide)1 << j;
            if (polynomial_flat_mul(a, b) != multiply_scalar(a, b)) {
                fprintf(stderr, "CLMUL basis mismatch at %u,%u\n", i, j);
                return 1;
            }
            ++checks;
        }
    }
    uint64_t random = UINT64_C(0x71a0cf6d58e9b234);
    for (unsigned i = 0; i < 1024; ++i) {
        uint64_t words[4];
        for (unsigned j = 0; j < 4; ++j) {
            random ^= random << 13;
            random ^= random >> 7;
            random ^= random << 17;
            words[j] = random;
        }
        const wide a = (wide)words[0] | (wide)words[1] << 64;
        const wide b = (wide)words[2] | (wide)words[3] << 64;
        if (polynomial_flat_mul(a, b) != multiply_scalar(a, b)) {
            fprintf(stderr, "CLMUL random mismatch at %u\n", i);
            return 1;
        }
        ++checks;
    }
    printf("PASS: %u CLMUL/scalar field products, including every 128-by-128 basis pair\n", checks);
    return 0;
}
