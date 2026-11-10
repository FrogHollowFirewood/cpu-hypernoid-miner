/* Independent first-round cache reuse/invalidation checks. */
#include "noid.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

static unsigned comparisons;

static int check_range(const noid_job *job, const uint8_t original[256],
                       uint64_t first, const uint8_t ns[8], const char *label)
{
    uint8_t actual[NOID_BATCH][32];
    noid_hash_range(job, first, ns, actual);
    for (unsigned lane = 0; lane < NOID_BATCH; ++lane) {
        uint8_t fields[256], expected[32];
        memcpy(fields, original, sizeof(fields));
        const uint64_t nonce = first + lane;
        memcpy(fields + 160, &nonce, 8);
        memcpy(fields + 168, ns, 8);
        noid_hash_reference(fields, expected);
        if (memcmp(actual[lane], expected, 32)) {
            fprintf(stderr, "cache mismatch %s first=%" PRIu64 " lane=%u\n",
                    label, first, lane);
            return 0;
        }
        ++comparisons;
    }
    return 1;
}

int main(void)
{
    uint8_t fields[256];
    uint8_t ns[8] = {0x12,0x34,0x56,0x78,0x9a,0xbc,0xde,0xf0};
    noid_job job;
    for (unsigned i = 0; i < sizeof(fields); ++i)
        fields[i] = (uint8_t)(i * 73 + 19);
    noid_prepare(&job, fields);
    const uint64_t starts[] = {0,64,128,192,128,0,256,320,384,448,256};
    for (unsigned i = 0; i < sizeof(starts) / sizeof(starts[0]); ++i)
        if (!check_range(&job, fields, starts[i], ns, "reuse/new coset")) return 1;

    /* Reuse the same job address and nonce coset while changing each
     * independent class of prepared data. A pointer-only cache would fail. */
    const unsigned changed[] = {0,176,224};
    for (unsigned i = 0; i < sizeof(changed) / sizeof(changed[0]); ++i) {
        fields[changed[i]] ^= 0xa5;
        noid_prepare(&job, fields);
        if (!check_range(&job, fields, 256, ns, "mutated job")) return 1;
        if (!check_range(&job, fields, 320, ns, "mutated job reuse")) return 1;
    }
    ns[7] ^= 0x80;
    if (!check_range(&job, fields, 256, ns, "new namespace")) return 1;
    if (!check_range(&job, fields, 320, ns, "new namespace reuse")) return 1;
    ns[7] ^= 0x80;
    if (!check_range(&job, fields, 256, ns, "restored namespace")) return 1;

    const uint64_t terminal = UINT64_MAX - 255;
    for (unsigned offset = 0; offset < 256; offset += 64)
        if (!check_range(&job, fields, terminal + offset, ns, "terminal coset")) return 1;
    if (!check_range(&job, fields, 63, ns, "unaligned fallback")) return 1;
    if (!check_range(&job, fields, 64, ns, "cache after fallback")) return 1;
    printf("PASS: %u scalar digest comparisons across cache reuse, invalidation, terminal coset and unaligned fallback\n", comparisons);
    return 0;
}
