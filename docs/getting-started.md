---
layout: default
title: Step-by-step Linux setup
---

# Step-by-step Linux setup

**Start with a verified release, check the machine offline, then enter your own pool and public receiving address.** CPU HyperNOID Miner v1.0.0 runs in a terminal as your normal Linux user. Extracting or building it does not install a service or start mining.

The release includes a **3% miner-use fee allocated by scheduled runtime**. Each run starts with your pool. Read the [fee policy](fees.md), including the published destination and what happens during a connection failure, before starting.

## 1. Check the operating system and processor architecture

Open a terminal on the machine that will mine:

```bash
uname -m
python3 --version
getconf GNU_LIBC_VERSION
```

| Architecture output | Release archive |
|---|---|
| `x86_64` | `cpu-hypernoid-miner-v1.0.0-linux-x86_64.tar.gz` |
| `aarch64` | `cpu-hypernoid-miner-v1.0.0-linux-aarch64.tar.gz` |
| Another value | No prebuilt v1.0.0 archive for that architecture |

The prebuilt release requires 64-bit Linux, glibc 2.34 or newer, Python 3.9 or newer, and working Linux pidfd support for reliable process cleanup. Ubuntu 22.04 is the native release-build target. Alpine/musl, 32-bit Linux, native Windows, and native macOS are outside the tested binary scope. See [hardware support](hardware.md) for the five engine requirements.

An ARM64 release asset is published only after the native ARM64 workflow passes for its exact source commit. Its `build/validation/ci-validation.json` records the completed checks. The ARM64 package uses the portable engine; it cannot run the optimized x86 engines.

## 2. Install the small set of runtime tools

On Ubuntu 22.04 or 24.04:

```bash
sudo apt-get update
sudo apt-get install -y python3 ca-certificates curl tar coreutils
```

Administrator access is needed for installing system packages, not for running the miner. The public launcher and client use Python's standard library; there is no separate Python package installation step.

## 3. Download and verify the archive

Use the project's [GitHub Releases page](https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/releases). Download the archive for your architecture and the release's `SHA256SUMS` file.

These commands download the **x86-64** release. For ARM64, replace `x86_64` in the archive name and extracted directory with `aarch64`:

```bash
mkdir -p "$HOME/hypernoid-downloads/v1.0.0"
cd "$HOME/hypernoid-downloads/v1.0.0"
curl --fail --location --remote-name https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/releases/download/v1.0.0/cpu-hypernoid-miner-v1.0.0-linux-x86_64.tar.gz
curl --fail --location --remote-name https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/releases/download/v1.0.0/SHA256SUMS
sha256sum --ignore-missing --check SHA256SUMS
```

Continue only when **the archive you downloaded** is reported as `OK`. The checksum list may cover other release assets that are not in your folder; `--ignore-missing` skips those. A successful check of an unrelated file is not enough.

Use the checksum file from the same trusted project release. Checksums detect changed files; they do not authenticate a download if an attacker replaces both the archive and its checksum. This release does not claim a signed-archive verification step.

Extract into a fresh directory. If a directory with this release name already exists, keep it separate rather than extracting over an existing installation:

```bash
tar -xzf cpu-hypernoid-miner-v1.0.0-linux-x86_64.tar.gz
cd cpu-hypernoid-miner-v1.0.0-linux-x86_64
sha256sum --check PACKAGE-SHA256SUMS
```

Every internal file check must pass. Keep the extracted folder together: the root command needs its launcher, policy, probe, manifest, and engine files.

## 4. Check the package without contacting a pool

Run from the extracted folder:

```bash
./hypernoid-miner --version
./hypernoid-miner --list-backends
./hypernoid-miner --check
./hypernoid-miner --self-test
```

`--list-backends` shows which engines your CPU and operating system support and which are installed. `--check` checks the package and selected runtime settings without starting a worker or making a network connection. Before you supply a pool, wallet, and thermal choice, it can report a ready package with an incomplete mining configuration.

`--self-test` performs bounded offline hashing and exits. It can use CPU resources, but it does not contact a pool or generate a payout. Stop and investigate a failed test before mining; see [troubleshooting](troubleshooting.md).

## 5. Enter your pool, address, and temperature policy

You need a Parano1d pool's **TLS hostname and port**, your **public Parano1d receiving address**, and a temperature cutoff suitable for your CPU and cooling. The receiving address starts with `o1`. Never enter a seed phrase, recovery phrase, or private key.

The following Bash prompts let you enter real settings without accidentally running a command containing example placeholders:

```bash
read -r -p 'TLS pool hostname:port: ' NOID_POOL
read -r -p 'Public Parano1d receiving address: ' NOID_WALLET
read -r -p 'Stop temperature in Celsius: ' NOID_STOP_C
./hypernoid-miner --check \
  --pool "$NOID_POOL" \
  --wallet "$NOID_WALLET" \
  --worker HomeCPU \
  --stop-temperature "$NOID_STOP_C"
```

This configured check still makes no pool connection. It can confirm a readable temperature sensor and show the fee policy, but it cannot confirm that your pool will authorize the address.

The launcher automatically looks for CPU hwmon sensors from `coretemp`, `k10temp`, `k8temp`, or `zenpower`. It stops if a requested guard cannot read a valid sensor. Some ARM boards expose only thermal-zone files, which this version does not accept. The [configuration guide](configuration.md#temperature-monitoring) explains supported explicit paths and the separate choice to run without this software guard.

There is no universal temperature default. In particular, 85°C is not a recommendation for every processor. Choose a limit using your CPU's specifications, cooling, and intended operating conditions.

## 6. Start the foreground miner

After the configured check passes, use the same settings:

```bash
./hypernoid-miner \
  --pool "$NOID_POOL" \
  --wallet "$NOID_WALLET" \
  --worker HomeCPU \
  --stop-temperature "$NOID_STOP_C"
```

The default thread count is the number of CPUs available to the process, capped at 1,024. To choose a count, add `--threads 24`, `--threads 32`, or another integer from 1 through 1,024. A smaller count can leave resources available for other work. Thread count does not force a particular backend.

Look for successful pool authorization and accepted shares. A displayed hashrate alone does not prove that the pool credits your work. Shares arrive at variable intervals, and pool difficulty affects how often they appear. Compare your user-phase accepted, rejected, and stale shares with your pool dashboard over a suitable period.

The launcher identifies user and fee phases in its output. Session counters can reset when it starts a fresh client. Fee-phase shares go to the disclosed fee address and must not be counted as credits to your wallet. The [fee policy](fees.md) explains the 97-minute user window and up-to-three-minute fee window.

## 7. Stop and choose how to run next time

Press **Ctrl+C once** and let cleanup finish. The launcher stops only its own client and worker processes. Do not start a second copy while the first is still shutting down.

For another foreground run, open a terminal, enter the extracted directory, and provide the settings again. The launcher does not save those shell variables as an application configuration file.

Optional user-service examples are included in `examples/`. Set up a service only after a successful foreground check and run. The sample deliberately prevents automatic restart after a guard failure. See [configuration](configuration.md#optional-user-service) and [updating](updating.md) before enabling unattended startup.
