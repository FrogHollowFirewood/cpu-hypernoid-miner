/* SPDX-License-Identifier: Apache-2.0 */
/* Exact first-round polynomial specialization for a 256-nonce subfield coset.
 * Derived directly from (b + a*z)^7 = sum(b^(7-k)*a^k*z^k), in characteristic2.
 * It returns the state after initial MDS, keyed full round0, and keyed MDS1.
 * A complete immutable job copy and full nonce coset key guard the TLS cache.
 */
#define POLY_TARGET __attribute__((target("avx512f,avx512bw,gfni,pclmul")))
static wide aes_to_flat_lut[32][16];
static uint64_t byte_multiply_maps[256];
static uint64_t byte_frobenius_maps[3][256];
static uint8_t polynomial_mix[4][8][4];
static uint8_t nonce_low_byte[256];
static int polynomial_compatible;

static uint8_t polynomial_byte_mul(uint8_t a, uint8_t b)
{
    uint8_t result=0;
    while (b) {
        if (b&1) result^=a;
        a=(uint8_t)((a<<1)^((a>>7)*0x1b));
        b>>=1;
    }
    return result;
}

static void polynomial_init(void)
{
    uint64_t inverse[128][2];
    for (unsigned bit=0;bit<128;bit++) {
        const wide flat=basis(load_constant(new2t[bit]),to_flat);
        inverse[bit][0]=(uint64_t)flat;
        inverse[bit][1]=(uint64_t)(flat>>64);
    }
    build_basis_table(aes_to_flat_lut,inverse);
    polynomial_compatible=1;
    __builtin_cpu_init();
    if (!__builtin_cpu_supports("pclmul")) polynomial_compatible=0;
    for (unsigned z=0;z<256;z++) {
        const wide encoded=basis(z,tower_to_aes);
        const uint8_t byte=(uint8_t)encoded;
        if (encoded != ((wide)byte*257)) polynomial_compatible=0;
        nonce_low_byte[z]=byte;
    }
    for (unsigned c=0;c<256;c++) {
        for(unsigned power=0;power<3;power++) {
            uint64_t matrix=0;
            for (unsigned bit=0;bit<8;bit++) {
                uint8_t x=(uint8_t)(1u<<bit);
                for(unsigned q=0;q<power;q++) x=polynomial_byte_mul(x,x);
                const unsigned p=polynomial_byte_mul((uint8_t)c,x);
                for (unsigned row=0;row<8;row++)
                    if(p&(1u<<row)) matrix|=UINT64_C(1)<<(8*(7-row)+bit);
            }
            byte_frobenius_maps[power][c]=matrix;
        }
        byte_multiply_maps[c]=byte_frobenius_maps[0][c];
    }
    uint8_t mds[4][4];
    for (unsigned i=0;i<4;i++) for (unsigned j=0;j<4;j++) {
        const wide encoded=basis(load_constant(noid_mds_full[i*4+j]),flat_to_aes);
        mds[i][j]=(uint8_t)encoded;
        if (encoded != ((wide)mds[i][j]*257)) polynomial_compatible=0;
    }
    for (unsigned j=0;j<4;j++) {
        uint8_t a_power=1;
        for (unsigned k=0;k<8;k++) {
            for (unsigned i=0;i<4;i++)
                polynomial_mix[i][k][j]=polynomial_byte_mul(mds[i][j],a_power);
            a_power=polynomial_byte_mul(a_power,mds[j][0]);
        }
    }
}

POLY_TARGET static wide polynomial_flat_mul(wide a,wide b)
{
    const __m128i av=_mm_set_epi64x((long long)(a>>64),(long long)a);
    const __m128i bv=_mm_set_epi64x((long long)(b>>64),(long long)b);
    const __m128i p0=_mm_clmulepi64_si128(av,bv,0x00);
    const __m128i p1=_mm_clmulepi64_si128(av,bv,0x11);
    const __m128i ax=_mm_xor_si128(av,_mm_shuffle_epi32(av,0x4e));
    const __m128i bx=_mm_xor_si128(bv,_mm_shuffle_epi32(bv,0x4e));
    const __m128i cross=_mm_xor_si128(_mm_clmulepi64_si128(ax,bx,0),_mm_xor_si128(p0,p1));
    const __m128i lo=_mm_xor_si128(p0,_mm_slli_si128(cross,8));
    const __m128i hi=_mm_xor_si128(p1,_mm_srli_si128(cross,8));
    const __m128i modulus=_mm_set_epi64x(0,0x87);
    const __m128i r0=_mm_clmulepi64_si128(hi,modulus,0x00);
    const __m128i r1=_mm_clmulepi64_si128(hi,modulus,0x01);
    const __m128i tail=_mm_clmulepi64_si128(_mm_srli_si128(r1,8),modulus,0);
    const __m128i reduced=_mm_xor_si128(_mm_xor_si128(lo,r0),_mm_xor_si128(_mm_slli_si128(r1,8),tail));
    uint64_t out[2];
    _mm_storeu_si128((__m128i*)out,reduced);
    return (wide)out[0]|(wide)out[1]<<64;
}

typedef struct {
    noid_job job;
    uint64_t first_coset, namespace_word;
    uint64_t maps[4][16][4];
    uint8_t constant[4][16];
    int valid;
} polynomial_cache;
static _Thread_local polynomial_cache first_poly_cache;

POLY_TARGET static void polynomial_prepare(polynomial_cache *cache,const noid_job *job,
                                           uint64_t first_coset,uint64_t namespace_word)
{
    __m128i original[4];
    for(unsigned i=0;i<4;i++)
        original[i]=_mm_loadu_si128((const __m128i*)job->prefix[i]);
    const wide nonce_aes=basis(((wide)namespace_word<<64)|first_coset,tower_to_aes);
    original[0]=_mm_xor_si128(original[0],_mm_set_epi64x((long long)(nonce_aes>>64),(long long)nonce_aes));
    original[1]=_mm_xor_si128(original[1],_mm_loadu_si128((const __m128i*)job->suffix[0]));
    const __m128i two=_mm_set1_epi64x((long long)NOID_AES_MUL_TWO);
    const __m128i four=_mm_set1_epi64x((long long)NOID_AES_MUL_FOUR);
    const __m128i u=_mm_xor_si128(original[0],original[1]);
    const __m128i v=_mm_xor_si128(original[2],original[3]);
    const __m128i b2=_mm_gf2p8affine_epi64_epi8(original[1],two,0);
    const __m128i d2=_mm_gf2p8affine_epi64_epi8(original[3],two,0);
    const __m128i r1=_mm_xor_si128(_mm_gf2p8affine_epi64_epi8(u,four,0),_mm_xor_si128(b2,v));
    const __m128i r3=_mm_xor_si128(_mm_gf2p8affine_epi64_epi8(v,four,0),_mm_xor_si128(d2,u));
    const __m128i mixed[4]={_mm_xor_si128(d2,_mm_xor_si128(u,r1)),r1,
                           _mm_xor_si128(b2,_mm_xor_si128(v,r3)),r3};
    __m128i powers[4][8];
    for(unsigned j=0;j<4;j++) {
        const __m128i keyed=_mm_xor_si128(mixed[j],_mm_loadu_si128((const __m128i*)round_aes[66*j]));
        uint8_t bytes[16];_mm_storeu_si128((__m128i*)bytes,keyed);
        const wide b=basis(read_le(bytes),aes_to_flat_lut);
        wide p[8];
        p[0]=1;p[1]=b;
        p[2]=polynomial_flat_mul(b,b);
        p[3]=polynomial_flat_mul(p[2],b);
        p[4]=polynomial_flat_mul(p[2],p[2]);
        p[5]=polynomial_flat_mul(p[4],b);
        p[6]=polynomial_flat_mul(p[3],p[3]);
        p[7]=polynomial_flat_mul(p[4],p[3]);
        for(unsigned k=0;k<8;k++) {
            const wide encoded=basis(p[k],flat_to_aes);
            powers[j][k]=_mm_set_epi64x((long long)(encoded>>64),(long long)encoded);
        }
    }
    memset(cache->maps,0,sizeof(cache->maps));
    for(unsigned i=0;i<4;i++) for(unsigned k=0;k<8;k++) {
        __m128i coefficient=_mm_setzero_si128();
        for(unsigned j=0;j<4;j++) {
            const __m128i matrix=_mm_set1_epi64x((long long)byte_multiply_maps[polynomial_mix[i][k][j]]);
            coefficient=_mm_xor_si128(coefficient,_mm_gf2p8affine_epi64_epi8(powers[j][7-k],matrix,0));
        }
        uint8_t bytes[16];
        _mm_storeu_si128((__m128i*)bytes,coefficient);
        for(unsigned byte=0;byte<16;byte++) {
            if(k==0) cache->constant[i][byte]=bytes[byte]^round_aes[66*i+1][byte];
            else {
                static const unsigned slot[8]={0,0,0,1,0,2,1,3};
                static const unsigned frobenius[8]={0,0,1,0,2,0,1,0};
                cache->maps[i][byte][slot[k]]^=byte_frobenius_maps[frobenius[k]][bytes[byte]];
            }
        }
    }
    cache->job=*job;
    cache->first_coset=first_coset;
    cache->namespace_word=namespace_word;
    cache->valid=1;
}

POLY_TARGET static void polynomial_first_round(vector state[4][16],const noid_job *job,
                                               uint64_t first,uint64_t namespace_word)
{
    polynomial_cache *cache=&first_poly_cache;
    const uint64_t coset=first&~UINT64_C(255);
    if(!cache->valid || cache->first_coset!=coset || cache->namespace_word!=namespace_word
            || memcmp(&cache->job,job,sizeof(*job)))
        polynomial_prepare(cache,job,coset,namespace_word);
    const vector z=_mm512_xor_si512(_mm512_load_si512(nonce_low_columns[0]),
                                  _mm512_set1_epi8((char)nonce_low_byte[first&255]));
    const vector z2=_mm512_gf2p8mul_epi8(z,z);
    const vector z3=_mm512_gf2p8mul_epi8(z2,z);
    const vector z4=_mm512_gf2p8mul_epi8(z2,z2);
    const vector z5=_mm512_gf2p8mul_epi8(z4,z);
    const vector z7=_mm512_gf2p8mul_epi8(z4,z3);
    for(unsigned i=0;i<4;i++) for(unsigned byte=0;byte<16;byte++) {
        const vector l1=_mm512_gf2p8affine_epi64_epi8(z,_mm512_set1_epi64((long long)cache->maps[i][byte][0]),0);
        const vector l3=_mm512_gf2p8affine_epi64_epi8(z3,_mm512_set1_epi64((long long)cache->maps[i][byte][1]),0);
        const vector l5=_mm512_gf2p8affine_epi64_epi8(z5,_mm512_set1_epi64((long long)cache->maps[i][byte][2]),0);
        vector output=_mm512_ternarylogic_epi64(l1,l3,l5,0x96);
        output=_mm512_xor_si512(output,_mm512_set1_epi8((char)cache->constant[i][byte]));
        if(byte<2) {
            const vector l7=_mm512_gf2p8affine_epi64_epi8(z7,_mm512_set1_epi64((long long)cache->maps[i][byte][3]),0);
            output=_mm512_xor_si512(output,l7);
        }
        state[i][byte]=output;
    }
}
