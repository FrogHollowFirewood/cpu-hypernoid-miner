/* SPDX-License-Identifier: Apache-2.0 */
#include "noid.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static uint64_t seed=0x0926badc00112233ULL;
static uint64_t next(void){seed^=seed<<13;seed^=seed>>7;seed^=seed<<17;return seed;}
int main(void){
 const unsigned prefixes[]={0,1,127,128,255,256,511,512,65535};
 uint8_t fields[256],space[8],full[64][32],found[64][32],match[64],target[32];
 noid_job job;unsigned long checked=0,hits=0,boundaries=0;
 for(unsigned trial=0;trial<8;++trial){
  for(unsigned i=0;i<256;++i) fields[i]=(uint8_t)next();
  for(unsigned i=0;i<8;++i) space[i]=(uint8_t)next();
  noid_prepare(&job,fields);
  for(unsigned batch=0;batch<64;++batch){
   const uint64_t first=(uint64_t)batch*64+(trial&1);
   noid_hash_range(&job,first,space,full);
   unsigned least=0;
   for(unsigned i=1;i<64;++i) if(noid_below_target(full[i],full[least])) least=i;
   for(unsigned limit=0;limit<sizeof(prefixes)/sizeof(prefixes[0])+2;++limit){
    if(limit<sizeof(prefixes)/sizeof(prefixes[0])){
     memset(target,255,sizeof(target));target[31]=prefixes[limit]>>8;target[30]=prefixes[limit];
    }else{
     memcpy(target,full[least],sizeof(target));
     if(limit==sizeof(prefixes)/sizeof(prefixes[0])+1)
      for(unsigned j=0;j<32;++j) if(++target[j]) break;
     if(target[31]<2) ++boundaries;
    }
    memset(match,0xa5,sizeof(match));
    noid_scan_range(&job,first,space,target,found,match);
    for(unsigned i=0;i<64;++i){
     int expected=noid_below_target(full[i],target);
     if(match[i]!=expected || (expected&&memcmp(found[i],full[i],32))){
      fprintf(stderr,"FAIL trial%u batch%u limit%u lane%u\n",trial,batch,limit,i);return 1;
     }
     ++checked;hits+=expected;
    }
   }
  }
 }
 printf("PASS: %lu target-filter comparisons; %lu positive hits; %lu exact/+1 narrow prefix boundaries\n",checked,hits,boundaries);
 return 0;
}
