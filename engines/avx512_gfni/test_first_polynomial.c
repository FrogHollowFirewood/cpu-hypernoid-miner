#include "noid.c"
#include <stdio.h>
static uint64_t random_state=UINT64_C(0x6e6f696431300017);
static uint64_t random64(void){random_state^=random_state<<13;random_state^=random_state>>7;random_state^=random_state<<17;return random_state;}
POLY_TARGET int main(void)
{
    uint8_t fields[256];
    for(unsigned i=0;i<256;i++)fields[i]=(uint8_t)random64();
    noid_job job;noid_prepare(&job,fields);
    if(!polynomial_compatible){fputs("unavailable\n",stderr);return 2;}
    for(unsigned i=0;i<4096;i++) {
        wide a=((wide)random64()<<64)|random64(),b=((wide)random64()<<64)|random64();
        if(polynomial_flat_mul(a,b)!=multiply_scalar(a,b)){fputs("CLMUL mismatch\n",stderr);return 1;}
    }
    unsigned lanes=0;
    for(unsigned trial=0;trial<128;trial++) {
        if(trial%16==0){for(unsigned i=0;i<256;i++)fields[i]=(uint8_t)random64();noid_prepare(&job,fields);}
        uint64_t first=random64()&~UINT64_C(255),ns=random64();
        if(trial==0)first=0;
        if(trial==1)first=UINT64_MAX&~UINT64_C(255);
        for(unsigned chunk=0;chunk<4;chunk++) {
            vector expected[4][16],actual[4][16];
            uint8_t columns[16][64];
            for(unsigned lane=0;lane<64;lane++) {
                const wide x=((wide)ns<<64)|(first+64*chunk+lane);
                uint8_t encoded[16];write_le(encoded,basis(x,tower_to_aes)^load_constant(job.prefix[0]));
                for(unsigned byte=0;byte<16;byte++)columns[byte][lane]=encoded[byte];
            }
            for(unsigned byte=0;byte<16;byte++) {
                expected[0][byte]=_mm512_loadu_si512(columns[byte]);
                expected[1][byte]=_mm512_set1_epi8(((uint8_t*)job.prefix[1])[byte]^((uint8_t*)job.suffix[0])[byte]);
                expected[2][byte]=_mm512_set1_epi8(((uint8_t*)job.prefix[2])[byte]);
                expected[3][byte]=_mm512_set1_epi8(((uint8_t*)job.prefix[3])[byte]);
            }
            noid_external_keyed_0(expected[0]);full_round_prekeyed(expected);noid_external_keyed_1(expected[0]);
            polynomial_first_round(actual,&job,first+64*chunk,ns);
            if(memcmp(actual,expected,sizeof(expected))) {fprintf(stderr,"first-round mismatch trial%u chunk%u\n",trial,chunk);return 1;}
            lanes+=64;
        }
    }
    printf("PASS: 4096 independent CLMUL products and %u first-round lane states, including cache hits, job changes, namespace changes and nonce endpoints\n",lanes);
}
