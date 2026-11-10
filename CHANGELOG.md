# Changelog

## 1.0.0

First public Linux release of CPU HyperNOID Miner.

- Packages the Prototype 10 AVX512/GFNI, AVX2/VPCLMUL, packed AVX2/PCLMUL, and SSSE3/PCLMUL engines behind one launcher.
- Adds a portable 64-bit C engine for older x86-64 CPUs and native Linux ARM64.
- Adds CPU and operating-system feature checks across the allowed CPU affinity, with a portable fallback and explicit backend overrides.
- Adds a certificate-verified TLS client, explicit user payout configuration, optional custom CA trust, and reference-share integration checks.
- Requires an explicit temperature policy for continuous mining. Supports monitored CPU hwmon inputs and stops on invalid or missing selected sensors.
- Adds runtime consistency checks, a per-user instance lock, owned-session cleanup using pidfds, and bounded offline self-tests.
- Adds a disclosed 3% miner-use fee allocated by scheduled mining time, with a published developer destination and explicit user/fee phase reporting.
- Publishes detailed Linux setup, build, configuration, troubleshooting, update, hardware, provenance, and validation guides.

The public version identifies the whole product. Historical engine-core versions remain visible for traceability. No universal hashrate, all-pool support, or dedicated ARM vector optimization is claimed.
