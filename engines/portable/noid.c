/* SPDX-License-Identifier: Apache-2.0
 * Modified for CPU HyperNOID Miner v1.0.0, November 2026.
 * Portable scalar backend: explicit byte order, software field arithmetic,
 * and a cached nonce-independent midstate. Upstream notices are in NOTICE.
 * Requires a 64-bit GCC/Clang target with unsigned __int128. No optional
 * AVX, GFNI or carry-less-multiplication CPU extension is required.
 */
#include "noid.h"

#include <limits.h>
#include <pthread.h>
#include <stddef.h>
#include <string.h>

#include "noid_tables.h"

#define ALWAYS_INLINE static inline __attribute__((always_inline))

typedef unsigned __int128 wide;

static wide to_flat[32][16], to_tower[32][16];
static pthread_once_t basis_ready = PTHREAD_ONCE_INIT;

static wide read_le(const uint8_t bytes[16])
{
    wide value = 0;
    for (unsigned i = 0; i < 16; ++i)
        value |= (wide)bytes[i] << (8 * i);
    return value;
}

static void write_le(uint8_t bytes[16], wide value)
{
    for (unsigned i = 0; i < 16; ++i)
        bytes[i] = (uint8_t)(value >> (8 * i));
}

static wide load_constant(const uint64_t pair[2])
{
    return (wide)pair[0] | (wide)pair[1] << 64;
}

static void build_basis_table(wide lut[32][16], const uint64_t matrix[128][2])
{
    for (size_t i = 0; i < 32; ++i) {
        for (unsigned j = 0; j < 16; ++j) {
            wide value = 0;
            for (size_t bit = 0; bit < 4; ++bit) {
                if (j & (1u << bit))
                    value ^= load_constant(matrix[i * 4 + bit]);
            }
            lut[i][j] = value;
        }
    }
}

ALWAYS_INLINE wide basis(wide x, const wide lut[32][16])
{
    const uint64_t lo = (uint64_t)x, hi = (uint64_t)(x >> 64);
    wide output = 0;
    for (size_t i = 0; i < 16; ++i)
        output ^= lut[i][(lo >> (4 * i)) & 15] ^ lut[i + 16][(hi >> (4 * i)) & 15];
    return output;
}

static void init_basis(void)
{
    build_basis_table(to_flat, noid_tower_to_flat);
    build_basis_table(to_tower, noid_flat_to_tower);
}

/* Direct polynomial multiplication in GF(2)[x] / (x^128 + x^7 + x^2 + x + 1).
 * This reference deliberately performs no midstate preparation or SIMD.
 */
static wide multiply_scalar(wide a, wide b)
{
    wide result = 0;
    while (b) {
        if (b & 1)
            result ^= a;
        const wide spill = a >> 127;
        a = (a << 1) ^ (0x87 * spill);
        b >>= 1;
    }
    return result;
}

static void scalar_matrix(wide state[4], const uint64_t m[16][2])
{
    const wide original[4] = { state[0], state[1], state[2], state[3] };
    for (size_t row = 0; row < 4; ++row) {
        state[row] = 0;
        for (size_t col = 0; col < 4; ++col)
            state[row] ^= multiply_scalar(original[col], load_constant(m[row * 4 + col]));
    }
}

static wide scalar_seventh(wide x)
{
    const wide square = multiply_scalar(x, x);
    const wide fourth = multiply_scalar(square, square);
    return multiply_scalar(multiply_scalar(x, square), fourth);
}

static void permute_scalar(wide state[4])
{
    scalar_matrix(state, noid_mds_full);
    for (size_t round = 0; round < 66; ++round) {
        const int partial = round >= 4 && round < 62;
        for (size_t i = 0; i < (partial ? 1u : 4u); ++i)
            state[i] = scalar_seventh(state[i] ^ load_constant(noid_round[i * 66 + round]));
        scalar_matrix(state, partial ? noid_mds_partial : noid_mds_full);
    }
}

static void absorb_scalar(wide state[4], const uint8_t fields[32])
{
    state[0] ^= basis(read_le(fields), to_flat);
    state[1] ^= basis(read_le(fields + 16), to_flat);
    permute_scalar(state);
}

void noid_hash_reference(const uint8_t fields[256], uint8_t hash[32])
{
    pthread_once(&basis_ready, init_basis);
    wide state[4] = { 0, 0, load_constant(noid_iv[0]), load_constant(noid_iv[1]) };
    for (size_t i = 0; i < 8; ++i)
        absorb_scalar(state, fields + i * 32);
    write_le(hash, basis(state[0], to_tower));
    write_le(hash + 16, basis(state[1], to_tower));
}

int noid_below_target(const uint8_t digest[32], const uint8_t target[32])
{
    const wide upper = read_le(digest + 16);
    const wide upper_target = read_le(target + 16);
    return upper == upper_target ? read_le(digest) < read_le(target) : upper < upper_target;
}



_Static_assert(CHAR_BIT == 8, "eight-bit bytes are required");
_Static_assert(sizeof(uint64_t) == 8, "64-bit uint64_t is required");
_Static_assert(sizeof(wide) == 16, "128-bit unsigned arithmetic is required");

/* Independent of the bit-by-bit oracle above. Horner evaluation consumes
 * four multiplier bits at a time in the same flat GF(2^128) representation.
 */
static wide multiply_portable(wide a, wide b)
{
    if (b == 0 || a == 0) return 0;
    if (b == 1) return a;
    if (a == 1) return b;
    static const unsigned short reduction[16] = {
        0x000, 0x087, 0x10e, 0x189, 0x21c, 0x29b, 0x312, 0x395,
        0x438, 0x4bf, 0x536, 0x5b1, 0x624, 0x6a3, 0x72a, 0x7ad
    };
    wide powers[4], table[16];
    powers[0] = a;
    for (unsigned i = 1; i < 4; ++i)
        powers[i] = (powers[i-1] << 1) ^ (0x87 * (powers[i-1] >> 127));
    table[0] = 0;
    for (unsigned i = 1; i < 16; ++i)
        table[i] = table[i & (i-1)] ^ powers[__builtin_ctz(i)];
    wide value = 0;
    for (int i = 31; i >= 0; --i) {
        unsigned spill = (unsigned)(value >> 124);
        value = (value << 4) ^ reduction[spill]
                ^ table[(unsigned)(b >> (4*i)) & 15];
    }
    return value;
}

/* A fixed-coefficient field product is linear in its input bits. These
 * nibble tables are shared, immutable after pthread_once, and CPU-neutral.
 */
static wide full_products[16][32][16];
static wide partial_products[16][32][16];
static pthread_once_t products_ready = PTHREAD_ONCE_INIT;

static void init_products(void)
{
    for (unsigned coefficient = 0; coefficient < 16; ++coefficient) {
        for (unsigned position = 0; position < 32; ++position) {
            for (unsigned digit = 0; digit < 16; ++digit) {
                wide value = (wide)digit << (4 * position);
                full_products[coefficient][position][digit] =
                    multiply_scalar(value, load_constant(noid_mds_full[coefficient]));
                partial_products[coefficient][position][digit] =
                    multiply_scalar(value, load_constant(noid_mds_partial[coefficient]));
            }
        }
    }
}

static void matrix_portable(wide state[4], const uint64_t coefficients[16][2],
                            const wide products[16][32][16])
{
    const wide original[4] = {state[0], state[1], state[2], state[3]};
    for (unsigned row = 0; row < 4; ++row) {
        wide value = 0;
        for (unsigned col = 0; col < 4; ++col) {
            unsigned index = row * 4 + col;
            wide coefficient = load_constant(coefficients[index]);
            if (coefficient == 1) value ^= original[col];
            else if (coefficient != 0) value ^= basis(original[col], products[index]);
        }
        state[row] = value;
    }
}

static wide seventh_portable(wide x)
{
    wide square = multiply_portable(x, x);
    wide fourth = multiply_portable(square, square);
    return multiply_portable(multiply_portable(x, square), fourth);
}

static void permute_portable(wide state[4])
{
    matrix_portable(state, noid_mds_full, full_products);
    for (unsigned round = 0; round < 66; ++round) {
        int partial = round >= 4 && round < 62;
        for (unsigned i = 0; i < (partial ? 1u : 4u); ++i)
            state[i] = seventh_portable(state[i] ^ load_constant(noid_round[i*66+round]));
        matrix_portable(state, partial ? noid_mds_partial : noid_mds_full,
                        partial ? partial_products : full_products);
    }
}

static void save_pair(uint64_t pair[2], wide value)
{
    pair[0] = (uint64_t)value;
    pair[1] = (uint64_t)(value >> 64);
}

int noid_cpu_supported(void) { return 1; }

void noid_prepare(noid_job *job, const uint8_t fields[256])
{
    pthread_once(&basis_ready, init_basis);
    pthread_once(&products_ready, init_products);
    wide state[4] = {0, 0, load_constant(noid_iv[0]), load_constant(noid_iv[1])};
    for (unsigned block = 0; block < 5; ++block) {
        state[0] ^= basis(read_le(fields + block * 32), to_flat);
        state[1] ^= basis(read_le(fields + block * 32 + 16), to_flat);
        permute_portable(state);
    }
    for (unsigned i = 0; i < 4; ++i) save_pair(job->prefix[i], state[i]);
    for (unsigned i = 0; i < 5; ++i)
        save_pair(job->suffix[i], basis(read_le(fields + (11 + i) * 16), to_flat));
}

static void hash_prepared(const noid_job *job, const uint8_t nonce[16], uint8_t hash[32])
{
    wide state[4];
    for (unsigned i = 0; i < 4; ++i) state[i] = load_constant(job->prefix[i]);
    state[0] ^= basis(read_le(nonce), to_flat);
    state[1] ^= load_constant(job->suffix[0]);
    permute_portable(state);
    for (unsigned i = 1; i < 5; i += 2) {
        state[0] ^= load_constant(job->suffix[i]);
        state[1] ^= load_constant(job->suffix[i+1]);
        permute_portable(state);
    }
    write_le(hash, basis(state[0], to_tower));
    write_le(hash + 16, basis(state[1], to_tower));
}

void noid_hash_batch(const noid_job *job, const uint8_t nonces[NOID_BATCH][16],
                    uint8_t hashes[NOID_BATCH][32])
{
    for (unsigned i = 0; i < NOID_BATCH; ++i)
        hash_prepared(job, nonces[i], hashes[i]);
}

void noid_hash_range(const noid_job *job, uint64_t first, const uint8_t namespace_bytes[8],
                    uint8_t hashes[NOID_BATCH][32])
{
    uint8_t nonce[16];
    memcpy(nonce + 8, namespace_bytes, 8);
    for (unsigned i = 0; i < NOID_BATCH; ++i) {
        uint64_t counter = first + i;
        for (unsigned byte = 0; byte < 8; ++byte)
            nonce[byte] = (uint8_t)(counter >> (8 * byte));
        hash_prepared(job, nonce, hashes[i]);
    }
}

void noid_scan_range(const noid_job *job, uint64_t first, const uint8_t namespace_bytes[8],
                    const uint8_t target[32], uint8_t hashes[NOID_BATCH][32],
                    uint8_t matches[NOID_BATCH])
{
    noid_hash_range(job, first, namespace_bytes, hashes);
    for (unsigned i = 0; i < NOID_BATCH; ++i)
        matches[i] = (uint8_t)noid_below_target(hashes[i], target);
}
