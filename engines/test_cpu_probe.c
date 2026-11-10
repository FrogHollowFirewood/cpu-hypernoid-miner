/* SPDX-License-Identifier: Apache-2.0 */
/* CPU HyperNOID Miner v1.0.0 capability-selection fixtures; no hashing. */
#define main public_probe_main
#include "cpu_probe.c"
#undef main
#include <assert.h>
int main(void)
{
    const unsigned all=0xff;
    for (size_t i=0;i<4;i++) assert(supports(i,all,true,true));
    assert(!supports(0,all,true,false));
    assert(!supports(0,all,false,false));
    assert(!supports(1,all,false,false));
    assert(!supports(2,all,false,false));
    assert(supports(3,all,false,false));
    for (size_t i=0;i<4;i++) assert(!supports(i,0,false,false));
    assert(supports(2,F_AVX|F_AVX2|F_PCLMUL,true,false));
    assert(!supports(1,F_AVX|F_AVX2|F_PCLMUL,true,false));
    assert(supports(1,F_AVX|F_AVX2|F_VPCLMUL,true,false));
    assert(!supports(0,all&~F_PCLMUL,true,true));
    assert(!supports(0,all&~F_GFNI,true,true));
    assert(!supports(0,all&~F_AVX512BW,true,true));
    assert(!supports(2,all&~F_PCLMUL,true,true));
    assert(!supports(2,all&~F_AVX2,true,true));
    assert(!supports(3,all&~F_SSSE3,true,true));
    /* Intersecting two heterogeneous CPUs must reject a feature missing on one. */
    const unsigned common=all&(all&~F_VPCLMUL&~F_AVX512F&~F_AVX512BW);
    assert(!supports(0,common,true,false));
    assert(!supports(1,common,true,false));
    assert(supports(2,common,true,false));
    puts("PASS: CPU/OS-state capability gates and heterogeneous intersection fixtures");
    return 0;
}
