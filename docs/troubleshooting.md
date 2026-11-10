---
layout: default
title: Troubleshooting
---

# Troubleshooting

**Start with the error printed immediately before the miner stops.** Check a verified package with `./hypernoid-miner --check` before changing settings. A failed guard should have a specific cause; repeatedly restarting it does not fix that cause.

The following commands do not mine or contact a pool:

```bash
uname -m
python3 --version
getconf GNU_LIBC_VERSION
./hypernoid-miner --version
./hypernoid-miner --list-backends
./hypernoid-miner --check
```

Run them from the extracted release directory as the same normal user who will mine. Keep the package together and use the root launcher.

## Download, integrity, or startup problems

| Symptom | What to check and do |
|---|---|
| Archive checksum is not `OK` | Stop before extracting or running it. Download the archive and matching checksum list from the trusted project release again. Make sure the filename and architecture match. |
| `PACKAGE-SHA256SUMS` reports a changed or missing file | Keep the failed directory for diagnosis and extract a fresh verified archive into a separate directory. Do not mix files from two versions. |
| Runtime manifest mismatch | A launcher, policy, probe, or worker differs from the manifest. Re-extract the official package. Do not run `make manifest` merely to hide an unexplained mismatch in a downloaded binary release. |
| `Exec format error` | Compare `uname -m` with the archive name. An x86-64 executable cannot run natively on ARM64, or the reverse. |
| `GLIBC_2.34 not found` or another missing GLIBC version | Compare your glibc with `build/build-info.json`. Use a compatible release environment or a checked native source build. Do not replace system library files by hand. |
| A worker says “No such file” although it exists | A missing ELF loader or incompatible libc can cause this. Check architecture and distribution compatibility; Alpine/musl is outside the tested prebuilt scope. |
| Permission denied when starting the launcher | Confirm the archive was extracted with executable permissions on a filesystem that permits execution. Use a normal user-owned local installation directory. |

Source developers may deliberately modify and rebuild files, run the relevant checks, and generate a new manifest. That is different from treating an unexplained integrity failure as harmless. See [building](building.md) and [provenance](provenance.md).

## Python, pidfds, or runtime-directory errors

The launcher requires Python 3.9 or newer with `os.pidfd_open` and `signal.pidfd_send_signal`, plus kernel support that actually permits these operations. A sufficiently new Python version alone does not prove the kernel or container policy allows them.

If process-identity checks fail, use a supported native Linux environment or ask the container administrator to review the relevant restriction. Do not remove cleanup checks or run the whole miner as root to work around an unexplained failure.

An invalid `$XDG_RUNTIME_DIR` may be reported if it is redirected, belongs to another user, or is writable by others. Start from a normal login as the intended mining user and inspect that user's session configuration. Avoid launching through `sudo` while retaining another user's environment.

## Unsupported engine or illegal instruction

`--list-backends` distinguishes **supported** from **installed**. A CPU may support an engine whose worker is absent from a package, and a package may include workers that this CPU cannot run. Auto selection needs both conditions and a matching runtime hash.

Use `--backend auto`, or select another listed supported engine such as portable. CPU model names, thread counts, and an advertised AVX-512 feature are not enough to infer the whole required feature set. Virtual machines can mask instructions, and the operating system must enable the necessary register state.

If a verified launcher path still encounters an illegal-instruction fault, stop using that build. Record the architecture, selected backend, and error. Check for a mixed archive or a custom compiler configuration before filing a report. Do not suppress feature checks or manually force a worker that the probe rejects.

## Another miner instance is already running

Only one HyperNOID launcher is allowed per user. If you started it in another terminal, stop that foreground run with Ctrl+C. If you use the sample service:

```bash
systemctl --user status hypernoid-miner.service
systemctl --user stop hypernoid-miner.service
```

Let cleanup finish before trying again. Do not delete an active lock file, launch another copy with elevated privileges, or kill unrelated miners to resolve this message.

## Temperature sensor or cutoff failures

Automatic discovery supports `coretemp`, `k10temp`, `k8temp`, and `zenpower`. Check the driver name and sensor labels under `/sys/class/hwmon`; do not assume a fixed hwmon number identifies the CPU.

An explicit input must resolve below `/sys/devices/` and have a `tempN_input` filename. `--temperature-file` also requires `--stop-temperature`. Thermal-zone paths such as `/sys/class/thermal/thermal_zone0/temp` are not supported. Some ARM boards therefore have no usable automatic guard in this release even though their portable backend works.

The threshold is in Celsius. sysfs inputs are read in millidegrees and converted by the launcher. A cutoff reached, disappeared sensor, read error, or invalid reading stops the session. That stop can occur in either the user or fee phase.

Check the sensor selection, cooling, workload, and hardware-specific limit before restarting. Reducing the number of threads may reduce load, but does not establish a safe temperature by itself. Do not simply raise the cutoff because it was reached. The explicit `--no-temperature-guard` option removes this software monitoring; it is not a repair for a faulty sensor or overheating system. See [temperature configuration](configuration.md#temperature-monitoring).

## TLS, connection, or authorization failures

| Message or behavior | Next step |
|---|---|
| Hostname cannot be resolved | Check spelling, network access, and the endpoint currently published by your pool. |
| Connection refused or repeatedly times out | Confirm the pool's TLS port and service availability. A web-dashboard port or plain Stratum port is not interchangeable. |
| Certificate verification or hostname failure | Check the system clock, installed CA certificates, and requested hostname. For a private user-pool CA, use a trusted PEM file with `--ca-file`. |
| Pool rejects authorization | Check your public `o1...` address and the pool's required worker format. Never substitute a private key or recovery phrase. |
| Unsupported protocol message or repeated invalid jobs | Confirm that the pool supports the client's Parano1d Stratum protocol. Universal compatibility with every pool is not claimed. |

Certificate and hostname checks remain enabled. There is no insecure-TLS switch to bypass an unexplained failure. A user's CA override affects only the user pool; the fee endpoint uses operating-system trust.

The configured `--check` action does not connect to the pool, so it cannot diagnose remote authorization or availability. With ordinary mining settings, the user client reconnects. `--once` makes an ordinary user-session disconnect end the run instead; a planned fee transition does not itself end it.

## Hashrate appears, but accepted shares are absent or low

A local hashrate is not a pool credit. Share timing varies with difficulty and chance, so there is no fixed time by which the first share must arrive. Check authorization, job receipt, accepted/rejected/stale counters, and your own pool dashboard over an appropriate interval.

Frequent rejections or stale shares need investigation. Include short error excerpts and the selected backend when reporting them. A successful self-test establishes the checked calculations; it does not guarantee every pool interaction or continuous stability on every machine.

Counters persist across ordinary reconnects within the same client process.
A phase switch starts a fresh client, and that new client's counters start
again; restarting the miner also starts new counters. Do not add fee-phase
accepted shares to the user's payout total. The fee is a schedule of up to
three minutes in each 100-minute cycle, not a promise of exactly 3% of shares
or revenue. See [fees](fees.md) for the destination and failure behavior.

## A service stays stopped or keeps restarting

Inspect the sample user service with:

```bash
systemctl --user status hypernoid-miner.service
journalctl --user -u hypernoid-miner.service -n 100 --no-pager
```

Status `78` indicates a launcher guard failure. The sample's `RestartPreventExitStatus=2 78` deliberately keeps that failure stopped. Status `2` usually means invalid command arguments or an unedited placeholder. Correct the reported problem before a manual restart; do not remove the restart protection.

After editing the unit file itself, run `systemctl --user daemon-reload`. After editing only `miner.env`, restart the service when ready. If systemd's start limit has been reached, resolve the fault first, then use `systemctl --user reset-failed hypernoid-miner.service` before starting it again.

## Information to include in a support report

Open an issue on the [project repository](https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/issues) with:

- Product version, Linux distribution, architecture, and selected backend.
- Whether you used an official binary archive or a native source build.
- The relevant `--check` or self-test result and a short sanitized error excerpt.
- Whether the failure occurred in a user phase, fee phase, or before any connection.
- Relevant source commit and build metadata for a custom build.

Review everything before posting. Do not include private keys, seed phrases, account passwords, a complete environment dump, or unrelated logs. A public receiving address is not a spending key, but you can still redact your own address when it is not needed to explain the issue.
