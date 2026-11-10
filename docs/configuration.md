---
layout: default
title: Configuration and command reference
---

# Configuration and command reference

**All mining settings are explicit command-line options.** Use the root `./hypernoid-miner` command so that package checks, CPU selection, the user lock, fee scheduling, temperature policy, and child-process cleanup remain active.

The official v1.0.0 release includes the [disclosed 3% scheduled-runtime fee](fees.md). Your `--pool`, `--wallet`, and `--worker` settings apply to your mining phase. The fixed fee policy is published in `launcher/fee-policy.json` and checked as part of package integrity.

## Command options

| Option | Default or requirement | Meaning |
|---|---|---|
| `--help` | Optional | Show the command syntax and fee summary, then exit. |
| `--version` | Optional | Show the public product version and fee disclosure. |
| `--list-backends` | Optional action | Report CPU/OS support, installed engines, and provenance without mining. |
| `--check` | Optional action | Check package integrity, backend, applicable runtime settings, sensor, and user lock; no network or hashing worker. |
| `--self-test` | Optional action | Run the selected engine's offline self-test with a 120-second bound; no pool connection. |
| `--backend ID` | `auto` | Choose a supported, installed, hash-listed engine. An override cannot bypass CPU checks. |
| `--pool HOST:PORT` | Required to mine | TLS pool endpoint for your phase. Certificate and hostname verification stay enabled. |
| `--wallet ADDRESS` | Required to mine | Your public Parano1d `o1...` receiving address. No private key or seed is needed. |
| `--worker LABEL` | `HyperNOID` | Pool worker label: 1–32 letters, digits, underscores, or hyphens. |
| `--threads N` | Available CPU affinity count, capped at 1,024 | Worker threads from 1 through 1,024, independent of backend. |
| `--ca-file PATH` | System certificate trust | Optional PEM trust file for your pool only. It does not change trust for the fee endpoint. |
| `--once` | Reconnect normally | Exit after an ordinary user pool session disconnects instead of reconnecting. Planned fee handovers and failed fee attempts do not themselves end the run. |
| `--stop-temperature C` | No universal default | Stop at or above your selected Celsius limit, or on a sensor failure. |
| `--no-temperature-guard` | Off | Explicitly run without the launcher's software temperature monitoring. |
| `--temperature-file PATH` | Auto-detect supported CPU sensors | Monitor an explicit hwmon `tempN_input`; repeat the option to monitor several and use their maximum. Requires `--stop-temperature`. |

Choose only one inspection action at a time. `--stop-temperature` and `--no-temperature-guard` are mutually exclusive. Actual mining requires a pool, receiving address, and one of those thermal choices. Inspection actions can run without a complete mining configuration; missing values are reported where relevant.

The numeric temperature parser accepts finite values greater than zero and no greater than 150°C. **That parsing range is not a safe operating range for your CPU.** Select a hardware-appropriate cutoff.

## Pool and receiving address

Use a TLS endpoint supported by the pool's Parano1d Stratum service. `HOST:PORT` is sufficient; `stratum+ssl://`, `stratum+tls://`, or `tls://` may prefix it. The client does not offer an option to disable certificate or hostname verification.

The pool determines whether an address is accepted and how shares, maturity, fees, and payout thresholds work. `--check` validates local configuration without contacting the pool, so it cannot confirm authorization or a future payout.

If your pool requires a private certificate authority, obtain its PEM certificate through a trusted channel and supply `--ca-file /path/to/pool-ca.pem`. This affects only the user session. Fee sessions use the operating system's default trusted certificate authorities.

The release has no seed-phrase or wallet-unlock setting. Your public receiving address appears in the command line and pool connection; do not treat it as a secret credential. Never paste a recovery phrase into an option or issue report.

## Choosing threads and an engine

Begin with `--backend auto`. The preference order is:

| Order | Backend | Required CPU features |
|---|---|---|
| 1 | `avx512_gfni` | AVX2, PCLMULQDQ, AVX512F, AVX512BW, GFNI, and usable OS vector state |
| 2 | `avx2_vpclmul256` | AVX2, VPCLMULQDQ, and usable OS AVX state |
| 3 | `avx2_pclmul_packed` | AVX2, PCLMULQDQ, and usable OS AVX state |
| 4 | `ssse3_pclmul128` | SSSE3 and PCLMULQDQ |
| 5 | `portable` | A compatible 64-bit Linux build |

Auto chooses the first supported engine that is present in the verified package. This is a capability preference, not a promise that the first engine is fastest on every CPU. ARM64 packages contain portable only. See [hardware support](hardware.md) for the conservative GFNI requirement and the difference between PCLMUL and VPCLMUL.

Inspect and test a supported override before using it:

```bash
./hypernoid-miner --list-backends
./hypernoid-miner --self-test --backend portable
```

For mining, add `--threads 24` or `--threads 32` to your complete command when those are your intended thread limits. You can choose a smaller count to leave CPU resources available. Choosing more threads than available logical CPUs is allowed by the numeric limit but does not create additional hardware or guarantee more useful work.

The probe checks the shared features of every CPU in the process's allowed affinity. The launcher preserves that probed set for its children. Container or administrator CPU restrictions therefore affect both default thread count and usable engines. Do not bypass the probe with a direct optimized-worker command.

## Temperature monitoring

With `--stop-temperature`, automatic discovery recognizes these CPU hwmon driver names: **`coretemp`, `k10temp`, `k8temp`, and `zenpower`**. It reads the selected inputs and stops if their maximum reaches the cutoff. A missing, unreadable, or invalid required sensor also stops the miner. The cutoff is a stop condition, not a temperature target or fan-control setting.

To inspect available hwmon driver names without changing the system:

```bash
for NOID_SENSOR_DIR in /sys/class/hwmon/hwmon*; do
  if [ -r "$NOID_SENSOR_DIR/name" ]; then
    printf '%s: ' "$NOID_SENSOR_DIR"
    cat "$NOID_SENSOR_DIR/name"
  fi
done
```

Use the driver name, input labels, and your system's documentation to identify actual CPU inputs. Do not assume that `hwmon0` or `temp1_input` always represents the CPU. hwmon numbering can change after a reboot.

An explicit path must resolve to a real file below `/sys/devices/` whose name matches `tempN_input`. A `/sys/class/hwmon/.../tempN_input` link can be accepted when it resolves to that valid location. Supply the path you actually inspected:

```bash
read -r -p 'Verified CPU hwmon input path: ' NOID_TEMP_FILE
./hypernoid-miner --check \
  --stop-temperature "$NOID_STOP_C" \
  --temperature-file "$NOID_TEMP_FILE"
```

This example assumes `NOID_STOP_C` contains your chosen cutoff, as in [getting started](getting-started.md). Repeat `--temperature-file` for several CPU inputs; the maximum controls the stop. sysfs values are in millidegrees Celsius: a raw value of `57000` means 57°C. Enter the cutoff in ordinary Celsius, not millidegrees.

Many ARM boards expose `/sys/class/thermal/thermal_zone*/temp` instead of supported hwmon inputs. **This release does not accept thermal-zone paths and does not promise automatic ARM sensor coverage.** A backend can be CPU-compatible while the software temperature guard is unavailable.

`--no-temperature-guard` is an explicit choice for a host where you have decided to rely on other monitoring and hardware controls. It removes this launcher's temperature cutoff; it does not add an alternative sensor or establish that a workload is safe. Do not combine it with `--temperature-file`. There is no hidden fallback from a failed requested guard to unmonitored mining.

## Fee phases, reconnects, and counters

The scheduled cycle starts with 97 minutes for your pool, followed by up to three minutes for the published fee destination. Connection, authorization, handover, and downtime consume their allotted windows. This is not a guarantee of an exact percentage of accepted shares or revenue.

Each phase starts a fresh client only after the previous owned client and workers are cleaned up. A fee connection failure skips the remaining fee window and returns to user work; there is no later catch-up fee. A guard or cleanup failure stops the launcher. Read [the complete fee policy](fees.md) for timing details and the public destination.

Counters persist across ordinary reconnects within the same client process.
They reset when a phase starts a fresh client or the miner process restarts.
Compare your own pool's credited work with user-phase output, and keep
fee-phase counters separate.

## Runtime state and exit codes

The launcher keeps one instance per user with a private runtime lock. It uses
`$XDG_RUNTIME_DIR` when that variable is set and valid. An invalid set value
stops the launcher; only an absent or empty value selects the per-user
directory under `/tmp`. Do not remove an active lock to force a second copy.
Stop the existing foreground process or user service first.

Python 3.9 or newer and working Linux pidfds are required for stable child-process identification. The launcher cleans up the client and workers it owns. It does not terminate unrelated miners or processes.

| Exit status | Meaning for an operator |
|---|---|
| `2` | Invalid command-line syntax or a missing/invalid option; correct the settings. |
| `78` | A launcher guard, integrity, capability, sensor, or cleanup failure; inspect the reported cause before restarting. |
| Other nonzero status | Inspect the accompanying client or self-test error; a child failure can be propagated. |

Press Ctrl+C for an ordinary foreground stop and allow cleanup to finish.

## Optional user service

The package includes `examples/miner.env.example`, `examples/hypernoid-miner.service`, and `examples/README.md`. They are templates, not an automatic installer. The environment file is read by systemd; the launcher itself does not load an `.env` file.

The sample uses a versioned installation under `~/opt/cpu-hypernoid-miner/`, runs as your normal user, and applies `RestartPreventExitStatus=2 78`. This keeps a temperature or integrity stop from becoming an automatic restart loop. Edit every placeholder, verify the same settings in a foreground run, and follow the included service instructions before enabling it.

The sample does not enable lingering after logout. If you deliberately want an always-running user service, review your distribution's user-service and login policy separately. See [updating](updating.md) for stopping the service and switching a verified version.
