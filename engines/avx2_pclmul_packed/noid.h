/* SPDX-License-Identifier: Apache-2.0 */
#ifndef NOID_CPU_KERNEL_H
#define NOID_CPU_KERNEL_H

#include <stdint.h>

#ifndef NOID_BATCH
#define NOID_BATCH 64
#endif

typedef struct {
    uint64_t prefix[4][2];
    uint64_t suffix[5][2];
} noid_job;

void noid_prepare(noid_job *job, const uint8_t fields[256]);
void noid_hash_batch(const noid_job *job, const uint8_t nonces[NOID_BATCH][16],
                     uint8_t hashes[NOID_BATCH][32]);
void noid_hash_range(const noid_job *job, uint64_t first, const uint8_t namespace_bytes[8],
                     uint8_t hashes[NOID_BATCH][32]);
void noid_scan_range(const noid_job *job, uint64_t first, const uint8_t namespace_bytes[8],
                     const uint8_t target[32], uint8_t hashes[NOID_BATCH][32],
                     uint8_t matches[NOID_BATCH]);
int noid_below_target(const uint8_t digest[32], const uint8_t target[32]);
void noid_hash_reference(const uint8_t fields[256], uint8_t hash[32]);
int noid_cpu_supported(void);

#endif
