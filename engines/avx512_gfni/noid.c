/* SPDX-License-Identifier: Apache-2.0 */
#include "noid.h"

#include <immintrin.h>
#include <pthread.h>
#include <stddef.h>
#include <string.h>

#include "noid_tables.h"
#include "noid_aes_tables.h"
#include "noid_fused_keys.h"

#define ALWAYS_INLINE static inline __attribute__((always_inline))
#define AVX512 __attribute__((target("avx512f,avx512bw,gfni")))


typedef unsigned __int128 wide;
typedef __m512i vector;

static wide to_flat[32][16], to_tower[32][16];
static uint8_t round_aes[264][16];
static uint32_t next_partial_key[58][16];
_Alignas(64) static uint8_t nonce_low_columns[16][NOID_BATCH];
static int trace_prefix_compatible;
static wide flat_to_aes[32][16], tower_to_aes[32][16], aes_to_tower[32][16];
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

static void polynomial_init(void);

static void init_basis(void)
{
    build_basis_table(to_flat, noid_tower_to_flat);
    build_basis_table(to_tower, noid_flat_to_tower);
    build_basis_table(flat_to_aes, f2new);
    build_basis_table(tower_to_aes, t2new);
    build_basis_table(aes_to_tower, new2t);
    for (size_t lane = 0; lane < NOID_BATCH; ++lane) {
        uint8_t encoded[16];
        write_le(encoded, basis(lane, tower_to_aes));
        for (size_t byte = 0; byte < 16; ++byte)
            nonce_low_columns[byte][lane] = encoded[byte];
    }
    /* The final tower digest's upper two bytes must be exactly the
     * projected trace coefficient before the fast final-round filter is
     * enabled. Keep a full-hash fallback if a future basis changes this. */
    trace_prefix_compatible = 1;
    for (unsigned bit = 0; bit < 112; ++bit)
        if (new2t[bit][1] >> 48)
            trace_prefix_compatible = 0;
    for (unsigned bit = 0; bit < 8; ++bit) {
        const uint16_t a = (uint16_t)(new2t[112+bit][1] >> 48);
        const uint16_t b = (uint16_t)(new2t[120+bit][1] >> 48);
        if ((a & 255) || (a >> 8) != (b & 255) || (a >> 8) != (b >> 8))
            trace_prefix_compatible = 0;
    }
    for (size_t i = 0; i < 264; ++i)
        write_le(round_aes[i], basis(load_constant(noid_round[i]), flat_to_aes));
    polynomial_init();
    // Incorporate the next partial round's key in the current mix output.
    // The final partial round leaves the original state for the full rounds.
    for (size_t i = 0; i < 57; ++i)
        for (size_t j = 0; j < 16; ++j)
            next_partial_key[i][j] = round_aes[5+i][j] * UINT32_C(0x01010101);
}

#include "first_polynomial.inc"

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

void noid_prepare(noid_job *job, const uint8_t fields[256])
{
    pthread_once(&basis_ready, init_basis);
    wide state[4] = { 0, 0, load_constant(noid_iv[0]), load_constant(noid_iv[1]) };
    for (size_t i = 0; i < 5; ++i)
        absorb_scalar(state, fields + i * 32);

    for (size_t i = 0; i < 4; ++i)
        write_le((uint8_t *)job->prefix[i], basis(state[i], flat_to_aes));
    for (size_t i = 0; i < 5; ++i)
        write_le((uint8_t *)job->suffix[i], basis(read_le(fields + 16*(11+i)), tower_to_aes));
}

int noid_avx512_supported(void)
{
    __builtin_cpu_init();
    return __builtin_cpu_supports("avx512f") && __builtin_cpu_supports("avx512bw")
        && __builtin_cpu_supports("gfni");
}

_Static_assert(NOID_BATCH == 64, "AES tower worker requires a 64-nonce batch");

AVX512 ALWAYS_INLINE vector vxor(vector a, vector b)
{
    return _mm512_xor_si512(a, b);
}

AVX512 ALWAYS_INLINE vector xor3(vector a, vector b, vector c)
{
    return _mm512_ternarylogic_epi64(a, b, c, 0x96);
}

AVX512 ALWAYS_INLINE vector linear(vector a, uint64_t matrix)
{
    return _mm512_gf2p8affine_epi64_epi8(a, _mm512_set1_epi64(matrix), 0);
}


/* Generated GFNI kernels evaluate the S-box and partial rounds. */
extern void noid_seventh_asm(vector *, const uint8_t *, uint64_t, uint64_t, uint64_t);
extern void noid_partial_rounds_asm(vector *, const uint32_t (*)[16], uint64_t, uint64_t, uint64_t);

extern void noid_seventh_trace16_asm(const vector *, vector *, uint64_t, uint64_t, uint64_t);

__attribute__((visibility("hidden"))) const uint64_t noid_sbox_matrix[] = {
    NOID_AES_MUL_ALPHA, NOID_AES_SQUARE_ALPHA, NOID_AES_SQUARE
};

__attribute__((visibility("hidden"))) const uint64_t noid_internal_matrix[] = {
    NOID_AES_DIAG_0,
    NOID_AES_DIAG_1_HIGH, NOID_AES_DIAG_1_SCALED,
    NOID_AES_DIAG_2_HIGH, NOID_AES_DIAG_2_SCALED,
    NOID_AES_DIAG_3_HIGH, NOID_AES_DIAG_3_SCALED,
    NOID_AES_NORM16,
    UINT64_C(0x133469c191224489), /* multiply 0x9d */
    UINT64_C(0x68b9738f76eddab4), /* multiply 0x2e */
    UINT64_C(0xa9faf4412a55aa54), /* multiply 0x29 */
    UINT64_C(0x2e72e4e6e2c58b17), /* multiply 0xe0 */
    UINT64_C(0x4adfbe3724499225), /* multiply 0xaa */
    UINT64_C(0x45cf9e78b468d1a2), /* multiply 0x43 */
    UINT64_C(0x44cd9a70a4489122), /* multiply 0x42 */
    UINT64_C(0x0e12244780010307), /* multiply 0xe8 */
    UINT64_C(0xa5eedd1f9a3469d2), /* fused multiply 0x4d */
    UINT64_C(0x9ea34613b973e7cf), /* fused multiply 0xfa */
    UINT64_C(0x2364c9b042840811), /* fused multiply 0x85 */
    UINT64_C(0x3f40803e43870f1f), /* fused multiply 0xf1 */
    UINT64_C(0x4ed2a50544891327), /* fused multiply 0xec */
    UINT64_C(0xa2e7cf3cdab468d1), /* fused multiply 0x86 */
    UINT64_C(0x6db66cb5060d1b36), /* fused multiply 0x69 */
    UINT64_C(0x9da74f02993367ce), /* fused multiply 0x77 */
    UINT64_C(0x3050a071d3a64c98), /* fused multiply 0x18 */
    UINT64_C(0x0819326cd0a14284), /* fused multiply 0x22 */
    UINT64_C(0x46cb9769942851a3), /* fused multiply 0xce */
    UINT64_C(0x1b2d5bad4183060d), /* fused multiply 0xbf */
    UINT64_C(0x769b3718478e1d3b), /* fused multiply 0xd6 */
    UINT64_C(0x9fa1421ba953a74f), /* fused multiply five */
};

__attribute__((visibility("hidden"))) const uint64_t noid_fused_matrix[] = {
    NOID_AES_MUL_TWO, NOID_AES_MUL_FOUR
};

AVX512 ALWAYS_INLINE void seventh(vector a[16], size_t round)
{
    noid_seventh_asm(a, round_aes[round], NOID_AES_MUL_ALPHA,
                    NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
}

AVX512 static void external(vector s[4][16])
{
    for (size_t i = 0; i < 16; ++i) {
        const vector u = vxor(s[0][i], s[1][i]);
        const vector v = vxor(s[2][i], s[3][i]);
        const vector b2 = linear(s[1][i], NOID_AES_MUL_TWO);
        const vector d2 = linear(s[3][i], NOID_AES_MUL_TWO);
        const vector r1 = xor3(linear(u, NOID_AES_MUL_FOUR), b2, v);
        const vector r3 = xor3(linear(v, NOID_AES_MUL_FOUR), d2, u);
        s[0][i] = xor3(d2, u, r1);
        s[1][i] = r1;
        s[2][i] = xor3(b2, v, r3);
        s[3][i] = r3;
    }
}

extern void noid_seventh_prekeyed_asm(vector *, const uint8_t *, uint64_t, uint64_t, uint64_t);
extern void noid_external_keyed_0(vector *);
extern void noid_external_keyed_1(vector *);
extern void noid_external_keyed_2(vector *);
extern void noid_external_keyed_3(vector *);
extern void noid_external_keyed_4(vector *);
extern void noid_external_sparse4_asm(vector *);
extern void noid_external_keyed_63(vector *);
extern void noid_external_keyed_64(vector *);
extern void noid_external_keyed_65(vector *);

extern void noid_seventh_external_dynamic_asm(vector *, const uint32_t *, uint64_t, uint64_t, uint64_t);

AVX512 static void full_round_prekeyed(vector s[4][16])
{
    for (size_t j = 0; j < 4; ++j)
        noid_seventh_prekeyed_asm(s[j], NULL, NOID_AES_MUL_ALPHA,
                                 NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
}

AVX512 static void partial_state_leave(vector s[4][16])
{
    /* T inverse has scalar third-column entries equal to the high byte of
     * its second column. Compute two words, then use u=s1+s2+s3. */
    for (size_t i=0; i<16; i+=2) {
        const vector u0=s[1][i+0];
        const vector u1=s[1][i+1];
        const vector v0=s[2][i+0];
        const vector v1=s[2][i+1];
        const vector z0=s[3][i+0];
        const vector z1=s[3][i+1];
        const vector vs=vxor(v0,v1), hz=vxor(v1,z1);
        const vector shared0=linear(vs,UINT64_C(0x93b56b45193264c9));
        const vector low0=xor3(vxor(u0,shared0),linear(v0,UINT64_C(0xaef3e6626ad5ab57)),linear(z0,UINT64_C(0x58e9d3fea54b962c)));
        const vector high0=xor3(u1,shared0,linear(hz,UINT64_C(0x58e9d3fea54b962c)));
        const vector shared1=linear(vs,UINT64_C(0x96ba747f69d2a54b));
        const vector low1=xor3(vxor(u0,shared1),linear(v0,UINT64_C(0x43c48853e4c89021)),linear(z0,UINT64_C(0x4dd6ac1464c99326)));
        const vector high1=xor3(u1,shared1,linear(hz,UINT64_C(0x4dd6ac1464c99326)));
        const vector low2=xor3(u0,low0,low1);
        const vector high2=xor3(u1,high0,high1);
        s[1][i]=low0;
        s[1][i+1]=high0;
        s[2][i]=low1;
        s[2][i+1]=high1;
        s[3][i]=low2;
        s[3][i+1]=high2;
    }
}
/* Only the last round of the last absorption can use this rejection.
 * Trace is linear, so its two coefficient vectors pass through the final
 * external matrix exactly. No original pre-S-box state is overwritten. */
AVX512 static uint64_t final_prefix_possible(const vector s[4][16], const uint8_t target[32])
{
    vector traces[4][2];
    for (size_t j = 0; j < 4; ++j)
        noid_seventh_trace16_asm(s[j], traces[j], NOID_AES_MUL_ALPHA,
                                 NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    vector upper[2];
    for (size_t i = 0; i < 2; ++i) {
        const vector u = vxor(traces[0][i], traces[1][i]);
        const vector v = vxor(traces[2][i], traces[3][i]);
        upper[i] = xor3(linear(u, NOID_AES_MUL_FOUR),
                        linear(traces[1][i], NOID_AES_MUL_TWO), v);
    }
    const vector high = linear(vxor(upper[0],upper[1]), NOID_AES_HIGH_BYTE_MAP);
    const vector low = linear(upper[1], NOID_AES_HIGH_BYTE_MAP);
    const vector limit_high = _mm512_set1_epi8((char)target[31]);
    const vector limit_low = _mm512_set1_epi8((char)target[30]);
    return _mm512_cmplt_epu8_mask(high,limit_high)
        | (_mm512_cmpeq_epu8_mask(high,limit_high)
           & _mm512_cmple_epu8_mask(low,limit_low));
}

AVX512 static int permutation(vector s[4][16], const uint8_t final_target[32], int first_prepared)
{
    if (!first_prepared) {
        noid_external_keyed_0(s[0]);
        for (size_t j=0; j<3; ++j)
            noid_seventh_prekeyed_asm(s[j], NULL, NOID_AES_MUL_ALPHA,
                                     NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
        noid_seventh_external_dynamic_asm(s[3], fused_external_keys[0], NOID_AES_MUL_ALPHA,
                                     NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    }
    for (size_t j=0; j<3; ++j)
        noid_seventh_prekeyed_asm(s[j], NULL, NOID_AES_MUL_ALPHA,
                                 NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    noid_seventh_external_dynamic_asm(s[3], fused_external_keys[1], NOID_AES_MUL_ALPHA,
                                 NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    for (size_t j=0; j<3; ++j)
        noid_seventh_prekeyed_asm(s[j], NULL, NOID_AES_MUL_ALPHA,
                                 NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    noid_seventh_external_dynamic_asm(s[3], fused_external_keys[2], NOID_AES_MUL_ALPHA,
                                 NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    full_round_prekeyed(s);
    noid_external_sparse4_asm(s[0]);
    noid_partial_rounds_asm(s[0], next_partial_key, NOID_AES_MUL_ALPHA,
                            NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    partial_state_leave(s);

    for (size_t j = 0; j < 4; ++j)
        seventh(s[j], 66*j + 62);
    noid_external_keyed_63(s[0]);
    for (size_t j=0; j<3; ++j)
        noid_seventh_prekeyed_asm(s[j], NULL, NOID_AES_MUL_ALPHA,
                                 NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    noid_seventh_external_dynamic_asm(s[3], fused_external_keys[3], NOID_AES_MUL_ALPHA,
                                 NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    for (size_t j=0; j<3; ++j)
        noid_seventh_prekeyed_asm(s[j], NULL, NOID_AES_MUL_ALPHA,
                                 NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    noid_seventh_external_dynamic_asm(s[3], fused_external_keys[4], NOID_AES_MUL_ALPHA,
                                 NOID_AES_SQUARE, NOID_AES_SQUARE_ALPHA);
    /* Wide targets use the complete final round directly. Narrow targets
     * get an exact two-byte prefix rejection; any possible hit falls back. */
    if (final_target && final_target[31] < 2 && trace_prefix_compatible
            && !final_prefix_possible(s, final_target))
        return 0;
    full_round_prekeyed(s);
    external(s);
    return 1;
}


int noid_below_target(const uint8_t digest[32], const uint8_t target[32])
{
    const wide upper = read_le(digest + 16);
    const wide upper_target = read_le(target + 16);
    return upper == upper_target ? read_le(digest) < read_le(target) : upper < upper_target;
}

/* Each vector is one coefficient byte across 64 independent nonces. The
 * highest digest byte is a linear projection of the second field element.
 * A strict prefix rejection leaves every potential share for full checking. */
AVX512 ALWAYS_INLINE uint64_t possible_lanes(const vector upper[16], uint8_t target)
{
    /* The high output byte is the field trace into GF(2^8): only the
     * highest conjugate pair contributes, through the same byte map. */
    const vector projected = linear(vxor(upper[14], upper[15]), NOID_AES_HIGH_BYTE_MAP);
    return _mm512_cmple_epu8_mask(projected, _mm512_set1_epi8((char)target));
}

AVX512 static void hash_aes(const noid_job *job, const uint8_t nonces[NOID_BATCH][16],
                           uint64_t first, uint64_t namespace_word,
                           uint8_t hashes[NOID_BATCH][32], const uint8_t target[32],
                           uint8_t matches[NOID_BATCH])
{
    vector state[4][16];
    const uint8_t (*prefix)[16] = (const uint8_t (*)[16])job->prefix;
    const uint8_t (*suffix)[16] = (const uint8_t (*)[16])job->suffix;
    const int use_polynomial = !nonces && polynomial_compatible;
    if (use_polynomial) {
        polynomial_first_round(state, job, first, namespace_word);
    } else {
        if (nonces) {
            uint8_t encoded[NOID_BATCH][16];
            for (size_t lane = 0; lane < NOID_BATCH; ++lane)
                write_le(encoded[lane], basis(read_le(nonces[lane]), tower_to_aes));
            for (size_t i = 0; i < 16; ++i) {
                uint8_t values[NOID_BATCH];
                for (size_t lane = 0; lane < NOID_BATCH; ++lane)
                    values[lane] = encoded[lane][i] ^ prefix[0][i];
                state[0][i] = _mm512_loadu_si512(values);
            }
        } else {
            /* A 64-aligned range shares its upper 122 nonce bits. */
            uint8_t common[16];
            const wide nonce = ((wide)namespace_word << 64) | first;
            write_le(common, basis(nonce, tower_to_aes) ^ load_constant(job->prefix[0]));
            for (size_t i = 0; i < 16; ++i)
                state[0][i] = vxor(_mm512_set1_epi8((char)common[i]),
                                   _mm512_load_si512(nonce_low_columns[i]));
        }
        for (size_t i = 0; i < 16; ++i) {
            state[1][i] = _mm512_set1_epi8(prefix[1][i] ^ suffix[0][i]);
            state[2][i] = _mm512_set1_epi8(prefix[2][i]);
            state[3][i] = _mm512_set1_epi8(prefix[3][i]);
        }
    
    }
    permutation(state, NULL, use_polynomial);
    for (size_t k = 1; k < 5; k += 2) {
        for (size_t j = 0; j < 2; ++j)
            for (size_t i = 0; i < 16; ++i)
                state[j][i] = vxor(state[j][i], _mm512_set1_epi8(suffix[k + j][i]));
        if (!permutation(state, k == 3 ? target : NULL, 0)) {
            if (matches)
                memset(matches, 0, NOID_BATCH);
            return;
        }
    }

    uint64_t possible = target ? possible_lanes(state[1], target[31]) : UINT64_MAX;
    if (matches)
        memset(matches, 0, NOID_BATCH);
    if (!possible)
        return;

    _Alignas(64) uint8_t columns[2][16][NOID_BATCH];
    for (size_t j = 0; j < 2; ++j)
        for (size_t i = 0; i < 16; ++i)
            _mm512_store_si512(columns[j][i], state[j][i]);

    const wide target_upper = target ? read_le(target + 16) : 0;
    while (possible) {
        const unsigned lane = (unsigned)__builtin_ctzll(possible);
        possible &= possible - 1;
        uint8_t encoded[16];
        for (size_t i = 0; i < 16; ++i)
            encoded[i] = columns[1][i][lane];
        const wide upper = basis(read_le(encoded), aes_to_tower);
        if (target && upper > target_upper)
            continue;
        for (size_t i = 0; i < 16; ++i)
            encoded[i] = columns[0][i][lane];
        const wide lower = basis(read_le(encoded), aes_to_tower);
        if (target && upper == target_upper && lower >= read_le(target))
            continue;
        write_le(hashes[lane], lower);
        write_le(hashes[lane] + 16, upper);
        if (matches)
            matches[lane] = 1;
    }
}

static void hash_range(const noid_job *job, uint64_t first, const uint8_t namespace_bytes[8],
                       uint8_t hashes[NOID_BATCH][32], const uint8_t target[32],
                       uint8_t matches[NOID_BATCH])
{
    uint64_t namespace_word;
    memcpy(&namespace_word, namespace_bytes, sizeof(namespace_word));
    if ((first & (NOID_BATCH - 1)) == 0) {
        hash_aes(job, NULL, first, namespace_word, hashes, target, matches);
        return;
    }

    uint8_t nonces[NOID_BATCH][16];
    for (size_t lane = 0; lane < NOID_BATCH; ++lane)
        write_le(nonces[lane], ((wide)namespace_word << 64) | (first + lane));
    hash_aes(job, nonces, 0, 0, hashes, target, matches);
}

void noid_hash_batch(const noid_job *job, const uint8_t nonces[NOID_BATCH][16],
                     uint8_t hashes[NOID_BATCH][32])
{
    hash_aes(job, nonces, 0, 0, hashes, NULL, NULL);
}

void noid_hash_range(const noid_job *job, uint64_t first, const uint8_t namespace_bytes[8],
                     uint8_t hashes[NOID_BATCH][32])
{
    hash_range(job, first, namespace_bytes, hashes, NULL, NULL);
}

void noid_scan_range(const noid_job *job, uint64_t first, const uint8_t namespace_bytes[8],
                     const uint8_t target[32], uint8_t hashes[NOID_BATCH][32],
                     uint8_t matches[NOID_BATCH])
{
    hash_range(job, first, namespace_bytes, hashes, target, matches);
}
