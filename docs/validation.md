---
layout: default
title: Validation
---

# CPU HyperNOID Miner v1.0.0 — validation

**All five backends passed their local Linux x86-64 correctness checks.** The
public launcher also passed 27 focused tests, including a real compiled worker
connected to a temporary localhost TLS server. The exact local artifact hashes
and test counts are recorded in the
[public validation record](https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/blob/main/validation/public-validation.json).

The launcher checks include the disclosed **3% scheduled-runtime miner-use fee**.
The fee tests use controlled clocks, phase outcomes, and real child-process
cleanup. They do not represent a full 100-minute mining cycle or live acceptance
by the fee pool.

These local results describe Linux x86-64. For a downloaded x86-64 or ARM64
release, consult its `build/validation/ci-validation.json` receipt and the linked
[native release workflow](https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/actions/workflows/validate.yml).
The receipt identifies the tested commit, architecture, phases, and runtime
manifest. No native ARM64 execution is claimed by this local record. Source and
build support alone do not establish an executed ARM64 result.

## What was checked

The local toolchain was GCC 14.2.0, Python 3.12.14, GNU Make 4.4.1, and GNU
binutils 2.44 on Linux x86-64. These versions describe the tested build, not the
only versions that can build the project. The local worker binaries and CPU
probe require glibc 2.34 or newer, based on their linked symbol versions. A build
made with another toolchain has its own binary hashes and runtime requirements.

| Backend | Completed local checks |
|---|---|
| `avx512_gfni` | Full digest and public-vector checks; target boundaries; polynomial and cache checks; exact sparse-transform checks; nonce allocation and accounting |
| `avx2_vpclmul256` | Full digest and public-vector checks; both field lanes; target boundaries; job transitions; nonce allocation; exact executable ISA audit |
| `avx2_pclmul_packed` | Full digest and public-vector checks; both field lanes; target boundaries; job transitions; nonce allocation; exact executable ISA audit |
| `ssse3_pclmul128` | Full digest and public-vector checks; field arithmetic; target boundaries; job transitions; nonce allocation; exact executable ISA audit |
| `portable` | Independent software-field checks; full digests and public vectors; target boundaries; job transitions; nonce allocation; comparison with an optimized backend |

Each backend passed **4,096 full digest comparisons**, **six public proof-of-work
vectors**, and **360,448 target comparisons**. The target cases include 34,090
positive hits and 374 narrow cases at an exact target or target plus one.
Equality is rejected: a share must satisfy the full 256-bit `digest < target`
comparison. Each backend also passed 1,664 comparisons covering prepared-job or
cache transitions, namespace changes, terminal ranges, and unaligned inputs.
These are per-backend counts; the same shared test cases are not presented as
different public consensus vectors.

The worker tests cover completed-work accounting, in-flight work at benchmark
shutdown, retargeting without repeated nonce ranges, and exhaustion before nonce
wrap. Instrumented allocation fixtures exercise groups of one and four batches.
They cover 4, 24, and 32 threads in the tower and portable engines, and four
threads in the AVX-512 engine's inherited fixture. These scheduling fixtures are
not performance measurements or hardware certification at those thread counts.

## Portable arithmetic and independent comparison

The portable backend's four-bit field multiplication was checked against the
separate bit-by-bit polynomial reference. Its dedicated suite passed:

- 4,096 products, squares, seventh powers, and explicit byte-order cases.
- All 128 field basis vectors and 4,096 fixed-coefficient products.
- 16 complete permutations against the reference implementation.

A separate local API comparison checked the portable engine against the
preserved packed AVX2 arithmetic. It passed **192 prepared-range digests**,
**192 arbitrary-nonce digests**, and **768 strict target decisions**. It used
three different jobs, zero and nonzero namespaces, all-ones namespace bytes,
unaligned input, and the last complete range before 64-bit nonce exhaustion.
Targets included zero, the maximum value, an exact digest, and that digest plus
one. Returned matching digests also agreed.

After that comparison, comments were corrected to describe the backend as
requiring no **optional AVX, GFNI, or PCLMUL extension**, rather than claiming
that no SIMD instruction can appear in an x86-64 program. Modification notices
and an endian-description comment were also added to the worker. No arithmetic
changed, and the rebuilt portable worker retained the same SHA-256:

```text
7faf752b753c5db1241dafc4be0dfbb873047cabf10ddaf0bb822de954f0781d
```

The final source hash and the earlier comparison source hash are both retained
in the public validation record so that this comment-only follow-up is explicit.

## Optimized arithmetic and instruction checks

Both packed two-lane engines passed 36,864 independent field products, 4,352
squares, and 1,536 fixed-coefficient products. The legacy XMM engine passed
18,432 products, 2,048 squares, and 768 fixed-coefficient products.

The AVX-512 suite additionally checked 65,536 trace-filter lanes, 32,768
first-round lane states, 4,096 polynomial-preparation products, and 17,408
field products including every pair of 128 basis vectors. Its exact sparse
transformation checks verified the matrix identities, all 33 hardcoded GFNI
maps, and the entry and exit implementations on every state basis vector.

Executable audits confirmed the intended separation between legacy XMM PCLMUL,
XMM PCLMUL combined with packed AVX2 operations, and native YMM VPCLMUL. The
baseline CPU probe passed an audit for unexpected optional instructions; its
XGETBV instruction is guarded by the required hardware and OS-support checks.
The portable worker's inspected executable contained none of the optional
AVX, PCLMUL, GFNI, AES, BMI/BMI2, or SSSE3 operations checked by the audit.

The probe checks the feature intersection across every CPU in the process's
allowed affinity. The launcher rechecks that CPU set before starting work and
pins the child to it. Tests cover missing OS AVX state, missing required CPU
features, and heterogeneous CPU feature sets. Backend ordering is a selection
policy; it does not guarantee the fastest backend on every processor.

## Launcher, TLS, and cleanup

The public launcher suite passed **27 tests with zero skips**. Coverage includes:

- Required user configuration, explicit thermal policy, and unsupported backend rejection.
- Package checksum changes, path traversal, changed CPU affinity, and a second instance for the same user.
- Temperature cutoff, missing sensors, and inaccessible unrelated process entries.
- Valid localhost TLS, an untrusted certificate, and a wrong hostname.
- A real portable worker finding a known reference share and advancing after a retarget.
- Separate handling of stale and invalid share responses.
- Cleanup of owned descendants, including a different process group in the same owned session, while an unrelated process stayed running.
- A real SIGTERM following the supervisor's normal stop path.
- Exact fee schedule boundaries, skipped windows, restarts, and delayed phase starts.
- Runtime-integrity failure between phases, and a real SIGTERM during planned cleanup that prevents the next phase.

The entry point compiles the checked launcher source bytes directly. It does
not execute an unchecked cached copy of that module. The runtime manifest covers
the probe, five built workers, root wrapper, inner entry point, launcher, Stratum
client, and fee policy. The final inspected files matched all eleven recorded
hashes. The selected worker, client, and policy are checked again before each
phase starts. These hashes detect inconsistent files; they do not authenticate
an untrusted distribution or its manifest.

The launcher-specific record, including exact policy and runtime hashes, is
available in
[launcher/VALIDATION.json](https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/blob/main/launcher/VALIDATION.json).

## Fee scheduling and accounting

The checks verify 5,820 scheduled seconds for the user followed by up to 180
scheduled seconds for the fee destination in each 6,000-second cycle. The user
phase comes first. Restarting starts a new user cycle, and missed fee windows
are skipped without catch-up. The published destination and schedule are
checked against the exact policy fixture.

Tests confirm that a fee failure returns to user work, the user's custom CA is
not used for the fee endpoint, and the user's `--once` setting survives planned
phase changes. Thermal, sensor, integrity, and cleanup failures stop work. Each
new phase starts only after the previous owned session has ended, with a fresh
client, worker, and set of session counters.

Fee cleanup begins up to four seconds before the absolute slot end. Its
graceful wait is capped by that end, and an already-expired phase cannot spawn
a child. Connection and cleanup time count inside the scheduled allocation.
Operating-system delays, termination latency, and in-flight work prevent an
exact bound on process lifetime or executed hashes. The tests establish the
disclosed scheduling behavior, not an exact 3% of accepted shares, revenue, or
payouts. See the [full fee policy](fees.md).

## Repeating the checks

From the source package root:

```bash
make all
make check-probe
make check-supported
NOID_TEST_WORKER="$PWD/engines/portable/noid-worker" python3 -B -m unittest discover -s launcher/tests -v
make manifest
```

`make check-supported` runs only backends that the current CPU and OS can use.
Those engine checks open no network sockets. The launcher tests use localhost
TLS and require the OpenSSL command-line program. They do not contact a public
pool. A clean build and its metadata are separate from the completed test results.

Release qualification additionally requires a clean committed source tree and
five successful phases: probe, supported engine suites, launcher tests, public
check, and public self-test. Packaging requires a receipt matching that commit,
native architecture, version, and runtime manifest. Documentation and source
inputs come from reviewed tracked files; the source archive is read from the
exact committed Git tree. CI also checks the extracted binary archive's full
checksum inventory and its public `--version` and `--check` commands. The
per-archive receipt and workflow result establish whether those gates completed
for a particular download.

## Limits of this evidence

The public runtime has not completed an external pool trial or a long-duration
stability run as part of this release validation. The local checks establish
correctness on the stated environment; they do not establish universal CPU or
distribution compatibility, sustained thermals, or mining profitability.
No full 100-minute real-world fee cycle or live fee-pool acceptance is claimed.

No hash rate is guaranteed. A verified 300 MH/s result has not been achieved.
The validation runs above are correctness checks, not performance benchmarks.
