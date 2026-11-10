#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "noid_aes_tables.h"
extern void noid_seventh_prekeyed_asm(void*,void*,uint64_t,uint64_t,uint64_t);
extern void noid_seventh_trace16_asm(const void*,void*,uint64_t,uint64_t,uint64_t);
static uint64_t rng=0x81609128badc0ffeULL;
static uint64_t next(void){rng^=rng<<13;rng^=rng>>7;rng^=rng<<17;return rng;}
int main(void){
 _Alignas(64) uint64_t original[128], full[128], out[16];
 for(unsigned test=0;test<1024;++test){
  for(unsigned i=0;i<128;++i) original[i]=test==0?0:test==1?UINT64_MAX:next();
  memcpy(full,original,sizeof(full));
  noid_seventh_trace16_asm(original,out,NOID_AES_MUL_ALPHA,NOID_AES_SQUARE,NOID_AES_SQUARE_ALPHA);
  noid_seventh_prekeyed_asm(full,0,NOID_AES_MUL_ALPHA,NOID_AES_SQUARE,NOID_AES_SQUARE_ALPHA);
  if(memcmp(out,&full[112],sizeof(out))){fprintf(stderr,"TRACE FAIL case %u\n",test);return 1;}
  uint64_t copy[128];memcpy(copy,original,sizeof(copy));
  noid_seventh_trace16_asm(original,out,NOID_AES_MUL_ALPHA,NOID_AES_SQUARE,NOID_AES_SQUARE_ALPHA);
  if(memcmp(copy,original,sizeof(copy))){fprintf(stderr,"INPUT MODIFIED case %u\n",test);return 1;}
 }
 puts("PASS: 65,536 trace16 lanes match full S-box; all source bytes preserved");return 0;
}
