---
layout: default
title: Updating and reverting a release
---

# Updating and reverting a release

**Keep each release in its own directory and verify the new one before replacing a working launch path.** The runtime manifest ties the launcher, fee policy, probe, and engine workers together. Copying a new executable into an older package can create an inconsistent installation.

Read the new release notes and [fee policy](fees.md), including any disclosed changes, before an update. Keep your own pool, public receiving address, worker label, thread limit, and thermal choice outside the extracted package. The launcher does not hold a private key or wallet balance that needs to be migrated.

## 1. Download a separate release

Open the project's [Releases page](https://github.com/FrogHollowFirewood/cpu-hypernoid-miner/releases), choose the archive matching `uname -m`, and obtain its matching `SHA256SUMS`. Use a fresh download directory for that version.

```bash
sha256sum --ignore-missing --check SHA256SUMS
```

Confirm that the exact archive you intend to use is reported as `OK`. Extract it into a new folder, enter that folder, and run:

```bash
sha256sum --check PACKAGE-SHA256SUMS
```

Do not extract over an old release. Keep its binaries and metadata together so that a rollback remains possible. An ARM64 asset must have native ARM64 validation for the release commit; consult `build/validation/ci-validation.json` in the new package.

## 2. Stop the old instance

For a foreground run, press Ctrl+C and wait for the client and worker cleanup to finish. For the optional sample service:

```bash
systemctl --user stop hypernoid-miner.service
systemctl --user status hypernoid-miner.service
```

Do not bypass the user lock or start the new package while the old instance is still active. A manual stop does not enable automatic startup for another copy.

## 3. Inspect and test the new package

From the new release directory:

```bash
./hypernoid-miner --version
./hypernoid-miner --list-backends
./hypernoid-miner --check
./hypernoid-miner --self-test
```

Then run a configured check with your normal options, including your thermal policy. Recheck an explicit sensor path after a reboot or system change because hwmon numbering can change. If you normally force a backend, confirm that the new package still reports it as both supported and installed.

Run the new version in the foreground with those settings. Confirm expected startup, pool authorization, phase labeling, and useful credited work. A short successful session does not establish long-term stability, but it can expose configuration or protocol problems before unattended operation.

If the check fails, leave the new version stopped and investigate the exact error. Do not rebuild its manifest to erase an unexplained integrity mismatch.

## 4. Switch a versioned user-service installation

This section applies only if you deliberately use the example user service. Its expected layout is:

| Path | Purpose |
|---|---|
| `~/opt/cpu-hypernoid-miner/versions/<release-directory>` | Complete extracted, verified release |
| `~/opt/cpu-hypernoid-miner/current` | Symbolic link to the chosen complete release |
| `~/.config/hypernoid-miner/miner.env` | Your user-pool and thermal settings |
| `~/.config/systemd/user/hypernoid-miner.service` | User service template with guard restart protection |

Create the `versions` directory if needed. Place the newly verified release there under its own original release-directory name. Use a destination that does not already contain another installation.

After the foreground test is complete and stopped, set a shell variable to that actual directory. For the first x86-64 release, the path would be:

```bash
NOID_RELEASE_DIR="$HOME/opt/cpu-hypernoid-miner/versions/cpu-hypernoid-miner-v1.0.0-linux-x86_64"
```

Use the correct new version and architecture for an update. Confirm that `"$NOID_RELEASE_DIR/hypernoid-miner"` exists and is the release you tested. With the service stopped, replace the `current` link:

```bash
if [ -L "$HOME/opt/cpu-hypernoid-miner/current" ] || [ ! -e "$HOME/opt/cpu-hypernoid-miner/current" ]; then
  ln -sfnT "$NOID_RELEASE_DIR" "$HOME/opt/cpu-hypernoid-miner/current"
else
  printf '%s\n' 'Stopped: current is a real file or directory; inspect it before changing the path.'
fi
```

This command does not replace a real `current` directory. Review the link before starting the service:

```bash
readlink -f "$HOME/opt/cpu-hypernoid-miner/current"
"$HOME/opt/cpu-hypernoid-miner/current/hypernoid-miner" --version
systemctl --user start hypernoid-miner.service
systemctl --user status hypernoid-miner.service
```

Changing only the target of `current` does not require reloading the unit. If you edit the service file itself, run `systemctl --user daemon-reload` before starting it. Preserve `RestartPreventExitStatus=2 78`; a guard failure must not become an automatic restart loop.

## Revert to a known working release

Stop the new foreground process or service and wait for cleanup. Verify the retained old package's checksums and settings, then run its offline checks. If it is still appropriate for the current pool protocol and your hardware, launch it directly or repoint the stopped service's `current` link to that complete directory.

Record which version is active. Do not copy only the old worker into the new package, and do not assume an older version supports a later consensus or pool-protocol change. Reverting software does not reverse pool payments or transfer funds between addresses.

## Updating a source build

Use a clean checkout or a separate worktree for the new release commit. Preserve local changes in your own branch rather than overwriting them. Follow [building and checking](building.md): rebuild the native workers, run the supported-engine checks and launcher tests, then regenerate the runtime manifest from the final files.

Source archives can be built and tested without Git metadata. Creating an official-style release archive with `tools/package_release.py` is stricter: it requires a clean Git commit and a matching native validation receipt for the same commit, architecture, and runtime manifest. This prevents old test evidence from qualifying changed package files.

Keep the old build until the new one has passed the relevant checks and a deliberate foreground trial. A successful compile alone is not evidence of mining correctness or better performance.
