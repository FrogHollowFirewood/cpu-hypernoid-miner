/* SPDX-License-Identifier: Apache-2.0 */
/* CPU HyperNOID Miner v1.0.0: baseline-safe Linux capability discovery.
 * Hardware probing never runs a hashing backend. XGETBV is guarded by the
 * architectural XSAVE, OSXSAVE and AVX flags; all allowed CPUs are intersected.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sched.h>
#include <unistd.h>
#if defined(__x86_64__)
#include <cpuid.h>
#endif

enum { F_SSSE3=1u<<0, F_PCLMUL=1u<<1, F_AVX=1u<<2, F_AVX2=1u<<3,
       F_AVX512F=1u<<4, F_AVX512BW=1u<<5, F_GFNI=1u<<6, F_VPCLMUL=1u<<7 };
struct backend { const char *name; unsigned required; bool avx, avx512; };
static const struct backend backends[] = {
    {"avx512_gfni", F_AVX2|F_PCLMUL|F_AVX512F|F_AVX512BW|F_GFNI, true, true},
    {"avx2_vpclmul256", F_AVX2|F_VPCLMUL, true, false},
    {"avx2_pclmul_packed", F_AVX2|F_PCLMUL, true, false},
    {"ssse3_pclmul128", F_SSSE3|F_PCLMUL, false, false},
};

static bool supports(size_t index, unsigned features, bool os_avx, bool os_avx512)
{
    const struct backend *b=&backends[index];
    return (features & b->required)==b->required
        && (!b->avx || os_avx) && (!b->avx512 || os_avx512);
}

#if defined(__x86_64__)
static void x86_features(unsigned *features, bool *os_avx, bool *os_avx512)
{
    unsigned a,b,c,d;
    unsigned maximum=__get_cpuid_max(0,NULL);
    *features=0; *os_avx=false; *os_avx512=false;
    if (maximum<1) return;
    __cpuid_count(1,0,a,b,c,d);
    if (c&(1u<<9)) *features|=F_SSSE3;
    if (c&(1u<<1)) *features|=F_PCLMUL;
    if (c&(1u<<28)) *features|=F_AVX;
    const unsigned state_flags=(1u<<26)|(1u<<27)|(1u<<28);
    if ((c&state_flags)==state_flags) {
        unsigned low,high;
        __asm__ volatile ("xgetbv" : "=a"(low), "=d"(high) : "c"(0) : "memory");
        const uint64_t xcr0=((uint64_t)high<<32)|low;
        *os_avx=(xcr0&UINT64_C(0x6))==UINT64_C(0x6);
        *os_avx512=*os_avx && (xcr0&UINT64_C(0xe6))==UINT64_C(0xe6);
    }
    if (maximum>=7) {
        __cpuid_count(7,0,a,b,c,d);
        if (b&(1u<<5)) *features|=F_AVX2;
        if (b&(1u<<16)) *features|=F_AVX512F;
        if (b&(1u<<30)) *features|=F_AVX512BW;
        if (c&(1u<<8)) *features|=F_GFNI;
        if (c&(1u<<10)) *features|=F_VPCLMUL;
    }
}
#endif

static const char *boolean(bool value) { return value ? "true" : "false"; }

int main(int argc,char **argv)
{
    if (argc!=1) {
        if (argc==2 && strcmp(argv[1],"--version")==0) {
            puts("CPU HyperNOID Miner v1.0.0 capability probe");
            return 0;
        }
        fputs("usage: cpu-probe [--version]\n",stderr);
        return 2;
    }
#if !defined(__linux__)
    fputs("This capability probe requires Linux.\n",stderr); return 2;
#endif
#if defined(__x86_64__)
    const char *architecture="x86_64";
#elif defined(__aarch64__)
    const char *architecture="aarch64";
#else
    const char *architecture="unknown";
#endif
    const bool portable=sizeof(void*)==8 && __BYTE_ORDER__==__ORDER_LITTLE_ENDIAN__
#if defined(__SIZEOF_INT128__)
        && __SIZEOF_INT128__==16
#else
        && false
#endif
        ;
    size_t bytes=128;
    cpu_set_t *original=NULL, *one=NULL;
    while (bytes<=1024*1024) {
        original=calloc(1,bytes);
        if (!original) { perror("allocate affinity mask"); return 2; }
        if (sched_getaffinity(0,bytes,original)==0) break;
        const int saved=errno;
        free(original); original=NULL;
        if (saved!=EINVAL) { errno=saved; perror("read CPU affinity"); return 2; }
        bytes*=2;
    }
    if (!original) { fputs("CPU affinity mask exceeds the supported bound.\n",stderr); return 2; }
    one=calloc(1,bytes);
    if (!one) { free(original); perror("allocate probe affinity"); return 2; }
    unsigned features=0xff;
    bool os_avx=true,os_avx512=true;
    unsigned count=0;
    int failure=0;
    for (size_t cpu=0;cpu<bytes*8;cpu++) {
        if (!CPU_ISSET_S(cpu,bytes,original)) continue;
        count++;
#if defined(__x86_64__)
        CPU_ZERO_S(bytes,one); CPU_SET_S(cpu,bytes,one);
        if (sched_setaffinity(0,bytes,one)!=0) { failure=errno; break; }
        unsigned current=0; bool current_avx=false,current_avx512=false;
        x86_features(&current,&current_avx,&current_avx512);
        features &= current;
        os_avx &= current_avx;
        os_avx512 &= current_avx512;
#else
        features=0; os_avx=false; os_avx512=false;
#endif
    }
    if (sched_setaffinity(0,bytes,original)!=0 && !failure) failure=errno;
    if (failure || !count) {
        if (failure) { errno=failure; perror("probe or restore CPU affinity"); }
        else fputs("No CPU is available in the current affinity mask.\n",stderr);
        free(one); free(original); return 2;
    }
    const char *preferred=NULL;
    printf("{\"schema\":1,\"architecture\":\"%s\",\"affinity_cpus\":%u,\"cpu_ids\":[",architecture,count);
    bool comma=false;
    for (size_t cpu=0;cpu<bytes*8;cpu++) if (CPU_ISSET_S(cpu,bytes,original)) {
        printf("%s%zu",comma?",":"",cpu); comma=true;
    }
    printf("],\"features\":{");
    const char *names[]={"ssse3","pclmulqdq","avx","avx2","avx512f","avx512bw","gfni","vpclmulqdq"};
    for (unsigned i=0;i<8;i++) printf("%s\"%s\":%s",i?",":"",names[i],boolean(features&(1u<<i)));
    printf("},\"os_avx\":%s,\"os_avx512\":%s,\"supported_backends\":[",boolean(os_avx),boolean(os_avx512));
    comma=false;
    for (size_t i=0;i<sizeof(backends)/sizeof(backends[0]);i++) if (supports(i,features,os_avx,os_avx512)) {
        printf("%s\"%s\"",comma?",":"",backends[i].name); comma=true;
        if (!preferred) preferred=backends[i].name;
    }
    if (portable) { printf("%s\"portable\"",comma?",":""); if (!preferred) preferred="portable"; }
    printf("],\"preferred_backend\":");
    if (preferred) printf("\"%s\"",preferred); else printf("null");
    puts(",\"feature_scope\":\"hardware intersection across current process affinity; OS state reported separately\"}");
    free(one); free(original);
    return 0;
}
