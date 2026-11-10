/* SPDX-License-Identifier: Apache-2.0 */
/* Modified for CPU HyperNOID Miner v1.0.0: public engine identification; mining behavior is unchanged. */
#define _POSIX_C_SOURCE 200809L

#include "noid.h"

#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifndef NOID_BATCH_GROUP
#define NOID_BATCH_GROUP 1
#endif
_Static_assert(NOID_BATCH_GROUP >= 1 && NOID_BATCH_GROUP <= 256,
               "NOID_BATCH_GROUP must be between 1 and 256");

/* Each worker owns whole contiguous groups. The cursor survives job and
 * target changes, so completed ranges are never repeated after a retarget. */
struct nonce_cursor {
    uint64_t base;
    uint64_t group_stride;
    unsigned group_offset;
};

static struct nonce_cursor nonce_cursor_begin(unsigned index, unsigned threads)
{
    const uint64_t span = (uint64_t)NOID_BATCH * NOID_BATCH_GROUP;
    return (struct nonce_cursor){ .base = (uint64_t)index * span,
                                  .group_stride = (uint64_t)threads * span };
}

static bool nonce_cursor_next(struct nonce_cursor *cursor)
{
    const bool within_group = cursor->group_offset + 1 < NOID_BATCH_GROUP;
    const uint64_t delta = within_group ? NOID_BATCH
        : cursor->group_stride - (uint64_t)(NOID_BATCH_GROUP - 1) * NOID_BATCH;
    if (UINT64_MAX - cursor->base < delta)
        return false;
    cursor->base += delta;
    cursor->group_offset = within_group ? cursor->group_offset + 1 : 0;
    return true;
}

enum { FIELD_BYTES = 256, NONCE_BYTES = 16, DIGEST_BYTES = 32, NONCE_OFFSET = 160 };

struct job {
    noid_job prepared;
    uint8_t fields[FIELD_BYTES];
    uint8_t target[DIGEST_BYTES];
    uint8_t nonce_namespace[8];
    uint64_t epoch;
};

struct run {
    pthread_mutex_t lock;
    pthread_cond_t changed;
    _Alignas(64) atomic_uint_fast64_t generation;
    atomic_bool quit;
    struct job job;
    unsigned threads;
    unsigned benchmark_ready;
    bool benchmark_full;
};

struct worker {
    /* Each worker publishes on its own cache line. No other worker writes it. */
    _Alignas(64) atomic_uint_fast64_t completed;
    struct run *run;
    unsigned index;
    uint64_t batches;
    uint64_t sink;
};

static double seconds(void)
{
    struct timespec stamp;
    clock_gettime(CLOCK_MONOTONIC, &stamp);
    return (double)stamp.tv_sec + (double)stamp.tv_nsec / 1000000000.0;
}

static int nibble(char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

static bool unhex(const char *text, uint8_t *bytes, size_t length)
{
    if (strlen(text) != length * 2)
        return false;
    for (size_t i = 0; i < length; ++i) {
        int hi = nibble(text[2 * i]);
        int lo = nibble(text[2 * i + 1]);
        if (hi < 0 || lo < 0)
            return false;
        bytes[i] = (uint8_t)((hi << 4) | lo);
    }
    return true;
}

static void nonce_at(uint8_t nonce[NONCE_BYTES], uint64_t counter,
                     const uint8_t namespace_bytes[8])
{
    // This build targets little-endian x86-64.
    memcpy(nonce, &counter, sizeof(counter));
    memcpy(nonce + 8, namespace_bytes, 8);
}

static void nonce_batch(uint8_t nonces[NOID_BATCH][NONCE_BYTES],
                        uint64_t first, const uint8_t namespace_bytes[8])
{
    for (unsigned i = 0; i < NOID_BATCH; ++i)
        nonce_at(nonces[i], first + i, namespace_bytes);
}

static void submit_hit(const struct job *job, const uint8_t nonce[NONCE_BYTES],
                       const uint8_t digest[DIGEST_BYTES])
{
    uint8_t fields[FIELD_BYTES];
    uint8_t checked[DIGEST_BYTES];
    memcpy(fields, job->fields, sizeof(fields));
    memcpy(fields + NONCE_OFFSET, nonce, NONCE_BYTES);
    noid_hash_reference(fields, checked);
    if (memcmp(checked, digest, DIGEST_BYTES) != 0) {
        fputs("fatal: candidate digest disagrees with scalar verification\n", stderr);
        exit(EXIT_FAILURE);
    }
    char hex[NONCE_BYTES * 2 + 1];
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < NONCE_BYTES; ++i) {
        hex[2 * i] = digits[nonce[i] >> 4];
        hex[2 * i + 1] = digits[nonce[i] & 15];
    }
    hex[NONCE_BYTES * 2] = '\0';
    printf("%" PRIu64 " %s\n", job->epoch, hex);
    fflush(stdout);
}

static void *search(void *data)
{
    struct worker *worker = data;
    struct run *run = worker->run;
    uint64_t seen = 0;
    uint64_t completed = 0;
    struct nonce_cursor cursor = nonce_cursor_begin(worker->index, run->threads);
    while (!atomic_load_explicit(&run->quit, memory_order_acquire)) {
        pthread_mutex_lock(&run->lock);
        while (seen == atomic_load_explicit(&run->generation, memory_order_relaxed)
               && !atomic_load_explicit(&run->quit, memory_order_relaxed))
            pthread_cond_wait(&run->changed, &run->lock);
        seen = atomic_load_explicit(&run->generation, memory_order_relaxed);
        struct job job = run->job;
        pthread_mutex_unlock(&run->lock);

        if (!job.epoch)
            continue;
        uint8_t hashes[NOID_BATCH][DIGEST_BYTES];
        uint8_t matches[NOID_BATCH];
        unsigned checked = 0;
        // Keep the cursor across job changes, including retargets of the same template.
        while (!atomic_load_explicit(&run->quit, memory_order_relaxed)
                 && atomic_load_explicit(&run->generation, memory_order_acquire) == seen) {
            const uint64_t base = cursor.base;
            noid_scan_range(&job.prepared, base, job.nonce_namespace, job.target, hashes, matches);
            checked += NOID_BATCH;
            if (checked >= 4096) {
                completed += checked;
                atomic_store_explicit(&worker->completed, completed, memory_order_relaxed);
                checked = 0;
            }
            for (unsigned i = 0; i < NOID_BATCH; ++i) {
                if (matches[i]
                    && atomic_load_explicit(&run->generation, memory_order_acquire) == seen) {
                    uint8_t nonce[NONCE_BYTES];
                    nonce_at(nonce, base + i, job.nonce_namespace);
                    submit_hit(&job, nonce, hashes[i]);
                }
            }
            if (!nonce_cursor_next(&cursor)) {
                fputs("fatal: exhausted worker nonce range\n", stderr);
                exit(EXIT_FAILURE);
            }
        }
        completed += checked;
        atomic_store_explicit(&worker->completed, completed, memory_order_relaxed);
    }
    return NULL;
}

static void *benchmark(void *data)
{
    struct worker *worker = data;
    struct run *run = worker->run;
    uint8_t hashes[NOID_BATCH][DIGEST_BYTES];
    struct nonce_cursor cursor = nonce_cursor_begin(worker->index, run->threads);
    uint64_t sink = 0;
    uint64_t batches = 0;
    pthread_mutex_lock(&run->lock);
    if (++run->benchmark_ready == run->threads)
        pthread_cond_broadcast(&run->changed);
    while (!atomic_load_explicit(&run->generation, memory_order_relaxed)
           && !atomic_load_explicit(&run->quit, memory_order_relaxed))
        pthread_cond_wait(&run->changed, &run->lock);
    pthread_mutex_unlock(&run->lock);
    while (!atomic_load_explicit(&run->quit, memory_order_relaxed)) {
        const uint64_t base = cursor.base;
        if (run->benchmark_full) {
            noid_hash_range(&run->job.prepared, base, run->job.nonce_namespace, hashes);
            for (unsigned i = 0; i < NOID_BATCH; ++i)
                if (noid_below_target(hashes[i], run->job.target))
                    sink ^= (uint8_t)(base + i);
            sink ^= hashes[batches % NOID_BATCH][0];
        } else {
            uint8_t matches[NOID_BATCH];
            noid_scan_range(&run->job.prepared, base, run->job.nonce_namespace,
                            run->job.target, hashes, matches);
            for (unsigned i = 0; i < NOID_BATCH; ++i)
                if (matches[i])
                    sink ^= hashes[i][0];
        }
        ++batches;
        if (!nonce_cursor_next(&cursor)) {
            fputs("fatal: exhausted benchmark nonce range\n", stderr);
            exit(EXIT_FAILURE);
        }
    }
    worker->batches = batches;
    worker->sink = sink;
    return NULL;
}

static void set_job(struct run *run, const struct job *job)
{
    pthread_mutex_lock(&run->lock);
    run->job = *job;
    atomic_fetch_add_explicit(&run->generation, 1, memory_order_release);
    pthread_cond_broadcast(&run->changed);
    pthread_mutex_unlock(&run->lock);
}

static bool read_job(char *line, struct job *job)
{
    char *next = NULL;
    char *epoch = strtok_r(line, " \t\r\n", &next);
    char *fields = strtok_r(NULL, " \t\r\n", &next);
    char *target = strtok_r(NULL, " \t\r\n", &next);
    char *namespace_text = strtok_r(NULL, " \t\r\n", &next);
    if (!epoch || !fields || !target || !namespace_text
        || strtok_r(NULL, " \t\r\n", &next))
        return false;
    char *end = NULL;
    errno = 0;
    job->epoch = strtoull(epoch, &end, 10);
    if (epoch[0] < '0' || epoch[0] > '9' || errno || *end || !job->epoch
        || !unhex(fields, job->fields, sizeof(job->fields))
        || !unhex(target, job->target, sizeof(job->target))
        || !unhex(namespace_text, job->nonce_namespace, sizeof(job->nonce_namespace)))
        return false;
    noid_prepare(&job->prepared, job->fields);
    return true;
}

static bool serve(struct run *run, const struct worker *workers)
{
    char *line = NULL;
    size_t capacity = 0;
    bool ok = true;
    double last_report = seconds();
    uint64_t previous = 0;
    while (getline(&line, &capacity, stdin) >= 0) {
        line[strcspn(line, "\r\n")] = '\0';
        if (strcmp(line, "quit") == 0)
            break;
        if (strcmp(line, "stats") == 0) {
            uint64_t count = 0;
            for (unsigned i = 0; i < run->threads; ++i)
                count += atomic_load_explicit(&workers[i].completed, memory_order_relaxed);
            double now = seconds();
            fprintf(stderr, "CPU wall rate %.3f MH/s over %.1f s | %" PRIu64 " checks total\n",
                    (double)(count - previous) / (now - last_report) / 1e6,
                    now - last_report, count);
            previous = count;
            last_report = now;
            continue;
        }
        struct job job = {0};
        if (strcmp(line, "stop") != 0 && !read_job(line, &job)) {
            fputs("invalid worker command\n", stderr);
            ok = false;
            break;
        }
        set_job(run, &job);
    }
    free(line);
    return ok;
}

static int self_test(void)
{
    uint8_t digest[DIGEST_BYTES] = {0};
    uint8_t target[DIGEST_BYTES] = {0};
    target[0] = 1;
    if (!noid_below_target(digest, target) || noid_below_target(target, target)
        || noid_below_target(target, digest)) {
        fputs("target boundary test failed\n", stderr);
        return EXIT_FAILURE;
    }
    uint64_t random = UINT64_C(0x4a61794e4f494421);
    for (unsigned case_number = 0; case_number < 64; ++case_number) {
        uint8_t fields[FIELD_BYTES];
        uint8_t nonces[NOID_BATCH][NONCE_BYTES];
        uint8_t hashes[NOID_BATCH][DIGEST_BYTES];
        noid_job job;
        for (size_t i = 0; i < sizeof(fields); ++i) {
            random ^= random << 13;
            random ^= random >> 7;
            random ^= random << 17;
            fields[i] = (uint8_t)random;
        }
        noid_prepare(&job, fields);
        uint64_t first = random & ~UINT64_C(63);
        if (case_number == 0)
            first = 0;
        else if (case_number == 1)
            first = UINT64_MAX - (NOID_BATCH - 1);
        else if (case_number % 4 == 0)
            ++first;
        nonce_batch(nonces, first, fields);
        noid_hash_range(&job, first, fields, hashes);
        uint8_t explicit_hashes[NOID_BATCH][DIGEST_BYTES];
        noid_hash_batch(&job, nonces, explicit_hashes);
        if (memcmp(hashes, explicit_hashes, sizeof(hashes)) != 0) {
            fputs("range and explicit nonce batches disagree\n", stderr);
            return EXIT_FAILURE;
        }

        /* Alternate an easy target, a realistic narrow target, and exact
         * equality to cover projection rejection and strict comparison. */
        if (case_number % 3 == 0) {
            memset(target, 0xff, sizeof(target));
        } else if (case_number % 3 == 1) {
            memset(target, 0, sizeof(target));
            target[DIGEST_BYTES - 2] = 0x80;
        } else {
            memcpy(target, hashes[case_number % NOID_BATCH], sizeof(target));
        }
        uint8_t matches[NOID_BATCH], scan_hashes[NOID_BATCH][DIGEST_BYTES];
        noid_scan_range(&job, first, fields, target, scan_hashes, matches);

        for (unsigned i = 0; i < NOID_BATCH; ++i) {
            uint8_t expected[DIGEST_BYTES];
            memcpy(fields + NONCE_OFFSET, nonces[i], NONCE_BYTES);
            noid_hash_reference(fields, expected);
            const int match = noid_below_target(expected, target);
            if (memcmp(expected, hashes[i], DIGEST_BYTES) != 0 || matches[i] != match
                || (match && memcmp(expected, scan_hashes[i], DIGEST_BYTES) != 0)) {
                fprintf(stderr, "digest mismatch at case %u lane %u\n", case_number, i);
                return EXIT_FAILURE;
            }
        }
    }
    printf("PASS: %u packed digests match the scalar reference\n", 64 * NOID_BATCH);
    return EXIT_SUCCESS;
}

static bool bounded_int(const char *text, unsigned upper, unsigned *out)
{
    char *end = NULL;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (text == end || *end || errno || value == 0 || value > upper)
        return false;
    *out = (unsigned)value;
    return true;
}

int main(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "--version") == 0) {
        puts("CPU HyperNOID Miner v1.0.0 (engine avx2_vpclmul256; core 0.10.1-rig-candidate)");
        return EXIT_SUCCESS;
    }
    if (!noid_cpu_supported()) {
        fputs("AVX2 and VPCLMULQDQ are required by this rig backend\n", stderr);
        return EXIT_FAILURE;
    }
    if (argc == 2 && strcmp(argv[1], "--self-test") == 0)
        return self_test();
    if (argc == 3 && strcmp(argv[1], "--digest") == 0) {
        uint8_t fields[FIELD_BYTES];
        uint8_t nonces[NOID_BATCH][NONCE_BYTES] = {{0}};
        uint8_t hashes[NOID_BATCH][DIGEST_BYTES];
        noid_job job;
        if (!unhex(argv[2], fields, sizeof(fields))) {
            fputs("invalid field schedule\n", stderr);
            return EXIT_FAILURE;
        }
        memcpy(nonces[0], fields + NONCE_OFFSET, NONCE_BYTES);
        noid_prepare(&job, fields);
        noid_hash_batch(&job, nonces, hashes);
        for (size_t i = 0; i < DIGEST_BYTES; ++i)
            printf("%02x", hashes[0][i]);
        putchar('\n');
        return EXIT_SUCCESS;
    }
    struct run run = {
        .lock = PTHREAD_MUTEX_INITIALIZER,
        .changed = PTHREAD_COND_INITIALIZER,
    };
    unsigned duration = 0;
    run.benchmark_full = argc == 4 && strcmp(argv[1], "--bench") == 0;
    bool bench = argc == 4 && (run.benchmark_full || strcmp(argv[1], "--bench-scan") == 0)
                 && bounded_int(argv[3], 3600, &duration);
    bool worker = argc == 3 && strcmp(argv[1], "--worker") == 0;
    if ((!bench && !worker) || !bounded_int(argv[2], 1024, &run.threads)) {
        fputs("usage: noid-worker --self-test | --digest FIELDS_HEX | --bench THREADS SECONDS | --bench-scan THREADS SECONDS | --worker THREADS\n", stderr);
        return EXIT_FAILURE;
    }
    if (bench) {
        for (size_t i = 0; i < sizeof(run.job.fields); ++i)
            run.job.fields[i] = (uint8_t)i;
        noid_prepare(&run.job.prepared, run.job.fields);
        run.job.target[DIGEST_BYTES - 4] = 1;
    }
    pthread_t ids[run.threads];
    struct worker workers[run.threads];
    unsigned started = 0;
    double begin = 0;
    for (; started < run.threads; ++started) {
        workers[started] = (struct worker){.run = &run, .index = started};
        int error = pthread_create(&ids[started], NULL, bench ? benchmark : search,
                                   &workers[started]);
        if (error) {
            fprintf(stderr, "could not start thread %u: %s\n", started, strerror(error));
            break;
        }
    }
    bool ok = started == run.threads;
    if (ok && bench) {
        pthread_mutex_lock(&run.lock);
        while (run.benchmark_ready != run.threads)
            pthread_cond_wait(&run.changed, &run.lock);
        begin = seconds();
        atomic_store_explicit(&run.generation, 1, memory_order_relaxed);
        pthread_cond_broadcast(&run.changed);
        pthread_mutex_unlock(&run.lock);
        struct timespec delay = {.tv_sec = duration};
        while (nanosleep(&delay, &delay) && errno == EINTR) {}
    } else if (ok) {
        ok = serve(&run, workers);
    }
    pthread_mutex_lock(&run.lock);
    atomic_store_explicit(&run.quit, true, memory_order_release);
    pthread_cond_broadcast(&run.changed);
    pthread_mutex_unlock(&run.lock);
    uint64_t batches = 0;
    uint64_t check = 0;
    for (unsigned i = 0; i < started; ++i) {
        pthread_join(ids[i], NULL);
        batches += workers[i].batches;
        check ^= workers[i].sink;
    }
    if (ok && bench) {
        /* Include every finished batch, including work completed after quit. */
        const double end = seconds();
        double elapsed = end - begin;
        uint64_t hashes = batches * NOID_BATCH;
        printf("%u threads | %" PRIu64 " hashes | %.3f s | %.3f MH/s | check=%" PRIx64 "\n",
               run.threads, hashes, elapsed, (double)hashes / elapsed / 1e6, check);
    }
    return ok ? EXIT_SUCCESS : EXIT_FAILURE;
}
