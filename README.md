# CPU HyperNOID Miner v1.0.0

**A Linux CPU miner for Parano1d, with automatic CPU selection and a portable fallback.**

CPU HyperNOID Miner brings the Prototype 10 CPU engines into one public package. It supports different thread counts through one command and selects an engine that the CPU and operating system can run. A separate ARM64 package uses the portable engine.

| Start here | Link |
|---|---|
| Download a release | [GitHub Releases](https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/releases) |
| Full documentation website | [CPU HyperNOID Miner](https://froghollowfirewood.github.io/cpu-hypernoid-miner/) |
| First installation | [Step-by-step Linux setup](docs/getting-started.md) |
| Every command option | [Configuration reference](docs/configuration.md) |
| Miner-use fee | [Disclosed 3% fee policy](docs/fees.md) |
| Supported CPUs and engine selection | [Hardware guide](docs/hardware.md) |
| Build and test from source | [Build guide](docs/building.md) |
| Fix a problem | [Troubleshooting](docs/troubleshooting.md) |
| Test evidence and limits | [Validation](docs/validation.md) |

This is an independent community project maintained by [FrogHollowFirewood](https://github.com/FrogHollowFirewood). It is not an official Parano1d release. The license is [Apache 2.0](LICENSE); the upstream attribution is preserved in [NOTICE](NOTICE).

## What the release includes

- Five engines in the Linux x86-64 package, covering newer and older 64-bit Intel and AMD CPUs.
- A Linux ARM64 package with the portable engine, qualified by the native ARM64 release workflow before publication.
- Automatic selection based on the features available across the process's allowed CPUs, including the operating system's vector-state support.
- Configurable mining threads from 1 through 1,024. The default is the number of CPUs available to the process, capped at 1,024.
- TLS pool connections with certificate and hostname verification.
- A user-selected temperature cutoff, plus an explicit option to run without the software temperature guard.
- Offline self-tests, package consistency checks, a single-instance user lock, and cleanup limited to the processes started by this miner.
- Full source, build instructions, retained upstream notices, release checksums, and validation evidence.

This release includes a **3% miner-use fee allocated by scheduled mining time**. The program and [fee policy](docs/fees.md) disclose its schedule, destination pool, and developer receiving address. Pool fees and pool payout rules are separate. Users supply their own public receiving address and pool for their mining time.

## Choose the right download

Run this in a terminal on the Linux machine that will mine:

```bash
uname -m
```

| Output | Download | Included engines |
|---|---|---|
| `x86_64` | `cpu-hypernoid-miner-v1.0.0-linux-x86_64.tar.gz` | All five engines below |
| `aarch64` | `cpu-hypernoid-miner-v1.0.0-linux-aarch64.tar.gz` | Portable |
| Anything else | No prebuilt v1.0.0 package | See the hardware guide |

The prebuilt packages require 64-bit Linux with glibc 2.34 or newer, Python 3.9 or newer, and working Linux pidfd support. Ubuntu 22.04 is the native CI build target for both architectures. Newer glibc-based distributions may work; check the release's build identity and run the local checks. A CPU model alone does not establish operating-system compatibility. Alpine/musl, 32-bit Linux, native Windows, and native macOS are outside the tested release scope.

### Engine coverage

| Engine ID | Required CPU features | Intended use |
|---|---|---|
| `avx512_gfni` | AVX2, AVX512F, AVX512BW, GFNI, PCLMULQDQ, and enabled OS vector state | Compatible newer x86 CPUs |
| `avx2_vpclmul256` | AVX2, VPCLMULQDQ, and enabled OS AVX state | Compatible AVX2 CPUs with vector carry-less multiplication |
| `avx2_pclmul_packed` | AVX2, PCLMULQDQ, and enabled OS AVX state | Broader AVX2 coverage |
| `ssse3_pclmul128` | SSSE3 and PCLMULQDQ | Older x86 CPUs without usable AVX2 |
| `portable` | Supported 64-bit Linux architecture | Fallback for x86-64 and the ARM64 package |

AVX-512 alone does not guarantee eligibility for the first row: the named extensions must also be present. The portable x86 build uses the normal x86-64 baseline and does not require optional AVX, GFNI, or carry-less multiplication instructions. The ARM64 release has no dedicated NEON, PMULL, or SVE mining optimization.

The order above is a compatibility preference. It is not a benchmark-based promise that the first engine will be fastest on every CPU. A 24-, 32-, or 192-thread machine uses the same public interface; it does not need a separate product version.

## Quick start

New to Linux mining? Follow the [full installation guide](docs/getting-started.md), which explains each step, the expected output, and how to recover from common errors.

### 1. Install the runtime tools

On Ubuntu 22.04 or 24.04:

```bash
sudo apt-get update
sudo apt-get install -y python3 ca-certificates curl tar coreutils
```

The miner itself runs as your normal user. It does not need administrator privileges.

### 2. Download, verify, and extract

The following example is for `x86_64`. For ARM64, replace each occurrence of `x86_64` in these commands with `aarch64`.

```bash
mkdir -p "$HOME/hypernoid-downloads"
cd "$HOME/hypernoid-downloads"
curl --fail --location --remote-name https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/releases/download/v1.0.0/cpu-hypernoid-miner-v1.0.0-linux-x86_64.tar.gz
curl --fail --location --remote-name https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/releases/download/v1.0.0/SHA256SUMS
sha256sum --ignore-missing --check SHA256SUMS
tar -xzf cpu-hypernoid-miner-v1.0.0-linux-x86_64.tar.gz
cd cpu-hypernoid-miner-v1.0.0-linux-x86_64
sha256sum --check PACKAGE-SHA256SUMS
```

Continue only if the archive you downloaded is reported as `OK` and the internal file checks pass. Download the checksums from the same trusted project release. Checksums detect changes; an attacker who replaces both a download and its checksum is outside that protection. Release signing is not claimed for v1.0.0.

### 3. Check the machine before connecting to a pool

```bash
./hypernoid-miner --version
./hypernoid-miner --list-backends
./hypernoid-miner --check
./hypernoid-miner --self-test
```

`--check` does not start a worker or make a network connection. Before you enter a wallet, pool, and thermal policy, it can report that the package is ready while the mining configuration is incomplete. `--self-test` performs bounded offline hashing and exits.

### 4. Enter your own mining settings

The prompts below keep placeholders out of the executable command. Enter a pool's **TLS** hostname and port, your public Parano1d receiving address, and a temperature limit appropriate for your CPU and cooling:

```bash
read -r -p 'TLS pool hostname:port: ' NOID_POOL
read -r -p 'Public Parano1d receiving address: ' NOID_WALLET
read -r -p 'Stop temperature in Celsius: ' NOID_STOP_C
./hypernoid-miner \
  --pool "$NOID_POOL" \
  --wallet "$NOID_WALLET" \
  --worker HomeCPU \
  --stop-temperature "$NOID_STOP_C"
```

This uses the available CPU count automatically. Add `--threads 24`, `--threads 32`, or another count when you want a specific thread limit. Never enter a seed phrase or private key: only a public receiving address is needed.

The launcher automatically looks for supported CPU temperature sensors. If it cannot read a valid sensor, it stops. The [configuration guide](docs/configuration.md) explains explicit sensor selection and the separate `--no-temperature-guard` choice. There is no universal default cutoff; 85°C is not presented as safe for every CPU.

### 5. Confirm useful work and stop cleanly

Look for pool authorization and increasing accepted-share counts. A displayed hashrate by itself does not establish that the pool is crediting work. Compare rejected or stale shares and the pool's own dashboard over an appropriate interval. Press **Ctrl+C** once to stop the launcher and its worker processes.

## Pool and protocol scope

The included client is a certificate-verified TLS Stratum nonce worker. Its development lineage was exercised with the InnovLab-style Parano1d Stratum protocol. The public automated suite tests a real compiled worker through a local TLS server, including reference shares, retargets, and certificate rejection.

Pool operators can implement different message formats or change endpoints. Confirm support with your pool. Plain TCP Stratum, direct solo-node RPC, and universal compatibility with every Parano1d pool are not claimed. This program does not replace the proving node described in the [official mining architecture](https://docs.parano1d.org/mining).

## Build, inspect, or contribute

```bash
git clone https://github.com/FrogHollowFirewood/cpu-hypernoid-miner.git
cd cpu-hypernoid-miner
git checkout v1.0.0
make all
make check-probe
make check-supported
NOID_TEST_WORKER="$PWD/engines/portable/noid-worker" \
  python3 -B -m unittest discover -s launcher/tests -v
```

See [building](docs/building.md) for prerequisites and architecture details, [provenance](docs/provenance.md) for the engine history, and [CONTRIBUTING](CONTRIBUTING.md) for a reviewable change process. CI runs correctness tests and temporary loopback TLS sessions. It performs no live pool mining.

## Performance and support

Correct digests, usable CPU dispatch, and accepted shares matter more than a marketing hashrate. This release makes no guaranteed MH/s claim. Performance depends on the CPU, memory system, thread count, frequency, cooling, operating system, selected engine, and workload. The portable engine favors compatibility and can be substantially slower than a specialized engine.

Read the [validation record](docs/validation.md) to distinguish local optimized-engine checks, native CI checks, and historical prototype experience. Those checks do not establish every CPU model, every pool, or long-term unattended stability.

For help, open a [GitHub issue](https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/issues). Include the version, Linux distribution, CPU architecture, selected backend, and a short sanitized error. Do not post credentials, private keys, seed phrases, or a complete environment dump. Update instructions are in [updating](docs/updating.md).
