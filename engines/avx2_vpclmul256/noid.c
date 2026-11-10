/* SPDX-License-Identifier: Apache-2.0 */
#include "noid.h"

#include <immintrin.h>
#include <pthread.h>
#include <stddef.h>
#include <string.h>

#include "noid_tables.h"
#include "noid_gf64_tables.h"

#define ALWAYS_INLINE static inline __attribute__((always_inline))
#define PCLMUL __attribute__((target("avx2,vpclmulqdq")))

typedef unsigned __int128 wide;
typedef __m128i vector;

static wide to_flat[32][16], to_tower[32][16];
static wide flat_to_tower64[32][16], to_tower64[32][16], from_tower64[32][16];
static pthread_once_t basis_ready = PTHREAD_ONCE_INIT;

static wide read_le(const uint8_t bytes[16])
{
    uint64_t words[2];
    memcpy(words, bytes, sizeof(words));
    return (wide)words[0] | (wide)words[1] << 64;
}

static void write_le(uint8_t bytes[16], wide value)
{
    const uint64_t words[2] = { (uint64_t)value, (uint64_t)(value >> 64) };
    memcpy(bytes, words, sizeof(words));
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
    build_basis_table(flat_to_tower64, noid_flat_to_tower64);
    build_basis_table(to_tower64, noid_tower_to_tower64);
    build_basis_table(from_tower64, noid_tower64_to_tower);
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

int noid_cpu_supported(void)
{
    __builtin_cpu_init();
    return __builtin_cpu_supports("avx2") && __builtin_cpu_supports("vpclmulqdq");
}

PCLMUL ALWAYS_INLINE vector vxor(vector a, vector b)
{
    return _mm_xor_si128(a, b);
}

PCLMUL ALWAYS_INLINE vector from_wide(wide x)
{
    return _mm_set_epi64x((long long)(x >> 64), (long long)x);
}

PCLMUL ALWAYS_INLINE wide to_wide(vector x)
{
    return (wide)(uint64_t)_mm_cvtsi128_si64(x)
         | (wide)(uint64_t)_mm_cvtsi128_si64(_mm_srli_si128(x, 8)) << 64;
}

PCLMUL ALWAYS_INLINE vector load_vector(const uint64_t *constant)
{
    return _mm_loadu_si128((const vector *)constant);
}

/* Two independent GF128 elements. Each has coefficients in the field
 * GF(2)[X]/(X^64 + X^4 + X^3 + X + 1), with Y^2 = XY + X.
 * Native 256-bit VPCLMULQDQ computes both lane products. The backend requires
 * AVX2 and VPCLMULQDQ, but no AVX-512 or GFNI instruction. */
typedef __m256i tower_pair;

PCLMUL ALWAYS_INLINE tower_pair pack(vector a, vector b)
{
    return _mm256_inserti128_si256(_mm256_castsi128_si256(a), b, 1);
}

PCLMUL ALWAYS_INLINE tower_pair pxor(tower_pair a, tower_pair b)
{
    return _mm256_xor_si256(a, b);
}

PCLMUL ALWAYS_INLINE tower_pair clmul_tower(tower_pair a, tower_pair b, const int control)
{
    return _mm256_clmulepi64_epi128(a,b,control);
}

PCLMUL ALWAYS_INLINE tower_pair clmul_tower_fixed(tower_pair a, vector b, const int control)
{
    return _mm256_clmulepi64_epi128(a,_mm256_broadcastsi128_si256(b),control);
}

/* Multiplication by X^64 folds through (1+X)(1+X^3). */
PCLMUL ALWAYS_INLINE tower_pair reduce_pair_tower(tower_pair a, tower_pair b)
{
    const tower_pair lo = _mm256_unpacklo_epi64(a, b);
    const tower_pair hi = _mm256_unpackhi_epi64(a, b);
    const tower_pair correction_table = _mm256_broadcastsi128_si256(_mm_setr_epi8(
        0x00, 0x1b, 0x2d, 0x36, 0x5a, 0x41, 0x77, 0x6c, 0xaf, 0xb4, 0x82, 0x99, 0xf5, 0xee, 0xd8, 0xc3));
    const tower_pair spill = _mm256_shuffle_epi8(correction_table, _mm256_srli_epi64(hi, 60));
    const tower_pair first = pxor(hi, _mm256_slli_epi64(hi, 1));
    const tower_pair folded = pxor(first, _mm256_slli_epi64(first, 3));
    return pxor(pxor(lo, spill), folded);
}

PCLMUL ALWAYS_INLINE tower_pair square(tower_pair a)
{
    const tower_pair lo = clmul_tower(a, a, 0x00);
    const tower_pair hi = _mm256_slli_epi64(clmul_tower(a, a, 0x11), 1);
    return reduce_pair_tower(pxor(lo, hi), hi);
}

PCLMUL ALWAYS_INLINE tower_pair cross_sum(tower_pair a)
{
    return pxor(a, _mm256_srli_si256(a, 8));
}

PCLMUL ALWAYS_INLINE tower_pair multiply(tower_pair a, tower_pair b)
{
    const tower_pair lo = clmul_tower(a, b, 0x00);
    const tower_pair hi = clmul_tower(a, b, 0x11);
    const tower_pair ab = clmul_tower(cross_sum(a), cross_sum(b), 0x00);
    const tower_pair hi_x = pxor(_mm256_slli_epi64(hi, 1), _mm256_slli_si256(_mm256_srli_epi64(hi, 63), 8));
    const tower_pair low = pxor(lo, hi_x);
    return reduce_pair_tower(low, pxor(pxor(low, hi), ab));
}

PCLMUL ALWAYS_INLINE tower_pair multiply_fixed(tower_pair a, vector b)
{
    return reduce_pair_tower(clmul_tower_fixed(a, b, 0x00), clmul_tower_fixed(a, b, 0x01));
}

static tower_pair tower_round[264];
static vector diagonal[4], two, four;

PCLMUL ALWAYS_INLINE tower_pair seventh(tower_pair a, size_t round)
{
    const tower_pair x = pxor(a, tower_round[round]);
    const tower_pair x2 = square(x);
    return multiply(multiply(x, x2), square(x2));
}

PCLMUL static void init_coefficients(void)
{
    for (size_t i = 0; i < 264; ++i)
        tower_round[i] = _mm256_broadcastsi128_si256(from_wide(basis(load_constant(noid_round[i]), flat_to_tower64)));
    for (size_t i = 0; i < 4; ++i)
        diagonal[i] = from_wide(basis(load_constant(noid_mds_partial[5*i]) ^ 1, flat_to_tower64));
    two = from_wide(basis(load_constant(noid_tower_to_flat[1]), flat_to_tower64));
    four = from_wide(basis(load_constant(noid_tower_to_flat[2]), flat_to_tower64));
}

/* These factorizations use the same rows as the reference matrices. */
PCLMUL ALWAYS_INLINE void external(tower_pair state[4])
{
    const tower_pair u = pxor(state[0], state[1]);
    const tower_pair v = pxor(state[2], state[3]);
    const tower_pair b2 = multiply_fixed(state[1], two);
    const tower_pair d2 = multiply_fixed(state[3], two);
    const tower_pair r1 = pxor(multiply_fixed(u, four), pxor(b2, v));
    const tower_pair r3 = pxor(multiply_fixed(v, four), pxor(d2, u));
    state[0] = pxor(r1, pxor(u, d2));
    state[1] = r1;
    state[2] = pxor(r3, pxor(v, b2));
    state[3] = r3;
}

PCLMUL ALWAYS_INLINE void internal(tower_pair state[4])
{
    const tower_pair sum = pxor(pxor(state[0], state[1]), pxor(state[2], state[3]));
    for (size_t i = 0; i < 4; ++i)
        state[i] = pxor(multiply_fixed(state[i], diagonal[i]), sum);
}


/* Independent nonces let the CPU overlap the carry-less multiply chains. */
#ifndef TOWER_GROUPS
#define TOWER_GROUPS 2
#endif
enum { PARALLEL_NONCES = 2*TOWER_GROUPS, PAIRS = TOWER_GROUPS };
_Static_assert(TOWER_GROUPS >= 1 && TOWER_GROUPS <= 16, "invalid independent chain count");

_Static_assert(NOID_BATCH % PARALLEL_NONCES == 0, "must use full parallel batches");

PCLMUL static void permutation_tower(tower_pair state[PAIRS][4])
{
    for (size_t k = 0; k < PAIRS; ++k)
        external(state[k]);
    for (size_t i = 0; i < 4; ++i) {
        for (size_t j = 0; j < 4; ++j)
            for (size_t k = 0; k < PAIRS; ++k)
                state[k][j] = seventh(state[k][j], 66*j + i);
        for (size_t k = 0; k < PAIRS; ++k)
            external(state[k]);
    }
    for (size_t i = 4; i < 62; ++i) {
        for (size_t k = 0; k < PAIRS; ++k)
            state[k][0] = seventh(state[k][0], i);
        for (size_t k = 0; k < PAIRS; ++k)
            internal(state[k]);
    }
    for (size_t i = 62; i < 66; ++i) {
        for (size_t j = 0; j < 4; ++j)
            for (size_t k = 0; k < PAIRS; ++k)
                state[k][j] = seventh(state[k][j], 66*j + i);
        for (size_t k = 0; k < PAIRS; ++k)
            external(state[k]);
    }
}

PCLMUL static void permutation(vector state[PARALLEL_NONCES][4])
{
    tower_pair packed[PAIRS][4];
    for (size_t k = 0; k < PAIRS; ++k)
        for (size_t i = 0; i < 4; ++i)
            packed[k][i] = pack(state[2*k][i], state[2*k+1][i]);
    permutation_tower(packed);
    for (size_t k = 0; k < PAIRS; ++k)
        for (size_t i = 0; i < 4; ++i) {
            state[2*k][i] = _mm256_castsi256_si128(packed[k][i]);
            state[2*k+1][i] = _mm256_extracti128_si256(packed[k][i], 1);
        }
}

static pthread_once_t backend_ready = PTHREAD_ONCE_INIT;
static void init_backend(void) { init_coefficients(); }

void noid_prepare(noid_job *job, const uint8_t fields[256])
{
    pthread_once(&basis_ready, init_basis);
    pthread_once(&backend_ready, init_backend);
    wide state[4] = { 0, 0, load_constant(noid_iv[0]), load_constant(noid_iv[1]) };
    for (size_t i = 0; i < 5; ++i)
        absorb_scalar(state, fields + i * 32);

    for (size_t i = 0; i < 4; ++i)
        write_le((uint8_t *)job->prefix[i], basis(state[i], flat_to_tower64));
    for (size_t i = 0; i < 5; ++i)
        write_le((uint8_t *)job->suffix[i], basis(read_le(fields + 16*(11+i)), to_tower64));
}

PCLMUL ALWAYS_INLINE void hash_range_kernel(const noid_job *job, const uint8_t nonces[NOID_BATCH][16],
                             uint64_t first, uint64_t namespace_word,
                             uint8_t hashes[NOID_BATCH][32], const uint8_t target[32],
                             uint8_t matches[NOID_BATCH], size_t lanes,
                             void (*permute)(vector [][4]),
                             const wide input_basis[32][16],
                             const wide output_basis[32][16])
{
    const vector prefix[4] = { load_vector(job->prefix[0]),
                               vxor(load_vector(job->prefix[1]), load_vector(job->suffix[0])),
                               load_vector(job->prefix[2]), load_vector(job->prefix[3]) };
    const wide upper_target = target ? read_le(target + 16) : 0;
    const wide lower_target = target ? read_le(target) : 0;

    for (size_t start = 0; start < NOID_BATCH; start += lanes) {
        vector state[lanes][4];
        for (size_t k = 0; k < lanes; ++k) {
            const size_t lane = start + k;
            const wide nonce = nonces ? read_le(nonces[lane])
                                      : ((wide)namespace_word << 64) | (first + lane);
            state[k][0] = vxor(prefix[0], from_wide(basis(nonce, input_basis)));
            for (size_t i = 1; i < 4; ++i)
                state[k][i] = prefix[i];
        }
        permute(state);
        for (size_t j = 1; j < 5; j += 2) {
            const vector a = load_vector(job->suffix[j]);
            const vector b = load_vector(job->suffix[j + 1]);
            for (size_t k = 0; k < lanes; ++k) {
                state[k][0] = vxor(state[k][0], a);
                state[k][1] = vxor(state[k][1], b);
            }
            permute(state);
        }

        for (size_t k = 0; k < lanes; ++k) {
            const size_t lane = start + k;
            const wide upper = basis(to_wide(state[k][1]), output_basis);
            if (matches)
                matches[lane] = 0;
            if (target && upper > upper_target)
                continue;
            const wide lower = basis(to_wide(state[k][0]), output_basis);
            if (target && upper == upper_target && lower >= lower_target)
                continue;
            write_le(hashes[lane], lower);
            write_le(hashes[lane] + 16, upper);
            if (matches)
                matches[lane] = 1;
        }
    }
}

PCLMUL static void hash_range(const noid_job *job, const uint8_t nonces[NOID_BATCH][16],
                             uint64_t first, uint64_t namespace_word,
                             uint8_t hashes[NOID_BATCH][32], const uint8_t target[32],
                             uint8_t matches[NOID_BATCH])
{
    hash_range_kernel(job, nonces, first, namespace_word, hashes, target, matches,
                      PARALLEL_NONCES, permutation, to_tower64, from_tower64);
}

void noid_hash_batch(const noid_job *job, const uint8_t nonces[NOID_BATCH][16],
                     uint8_t hashes[NOID_BATCH][32])
{
    hash_range(job, nonces, 0, 0, hashes, NULL, NULL);
}

void noid_hash_range(const noid_job *job, uint64_t first, const uint8_t namespace_bytes[8],
                     uint8_t hashes[NOID_BATCH][32])
{
    uint64_t namespace_word;
    memcpy(&namespace_word, namespace_bytes, sizeof(namespace_word));
    hash_range(job, NULL, first, namespace_word, hashes, NULL, NULL);
}

void noid_scan_range(const noid_job *job, uint64_t first, const uint8_t namespace_bytes[8],
                     const uint8_t target[32], uint8_t hashes[NOID_BATCH][32],
                     uint8_t matches[NOID_BATCH])
{
    uint64_t namespace_word;
    memcpy(&namespace_word, namespace_bytes, sizeof(namespace_word));
    hash_range(job, NULL, first, namespace_word, hashes, target, matches);
}
