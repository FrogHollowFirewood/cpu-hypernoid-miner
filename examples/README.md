# Optional user-service examples

These templates are for a Linux system using a systemd user manager. They are not installed or enabled automatically. Complete the [foreground setup](../docs/getting-started.md), read the [3% fee policy](../docs/fees.md), and verify your selected temperature sensor before enabling unattended operation.

The service runs as your normal user. It expects a complete verified release in `~/opt/cpu-hypernoid-miner/versions/` and a `current` symbolic link pointing to it. It uses automatic backend selection and a required temperature cutoff. It does not disable thermal monitoring, change CPU power limits, or enable lingering after logout.

## 1. Prepare the versioned installation

Create the parent directories:

```bash
mkdir -p "$HOME/opt/cpu-hypernoid-miner/versions"
mkdir -p "$HOME/.config/systemd/user"
install -d -m 700 "$HOME/.config/hypernoid-miner"
```

Place the complete extracted and checksum-verified release into the `versions` directory using its original release-folder name. Do not merge it into an existing release. Follow the link-selection instructions in [updating](../docs/updating.md#4-switch-a-versioned-user-service-installation) to point `current` at the exact release you checked.

## 2. Create your settings file

From the verified release root, copy the example only if you do not already have a settings file you want to keep:

```bash
install -m 600 examples/miner.env.example "$HOME/.config/hypernoid-miner/miner.env"
```

Edit that file with your text editor. Replace the pool, public receiving address, thread count, worker label, and temperature cutoff with the settings you tested in the foreground. The placeholder address and cutoff deliberately cannot start a valid mining run.

This is a systemd `EnvironmentFile`, not a shell script. Do not add `export`, command substitutions, or private wallet material. The launcher does not automatically load this file for foreground commands. The service passes its values as explicit options.

The template uses the standard auto-detected CPU hwmon inputs. If your machine needs a supported explicit input, edit the service's `ExecStart` to include the exact `--temperature-file` you verified. See the [temperature requirements](../docs/configuration.md#temperature-monitoring). A thermal-zone-only ARM board cannot use this guarded template unchanged.

## 3. Install the user unit

From the same release root:

```bash
install -m 644 examples/hypernoid-miner.service "$HOME/.config/systemd/user/hypernoid-miner.service"
systemctl --user daemon-reload
```

The unit uses `Restart=on-failure`, but excludes exit statuses **2 and 78** from restart. Status 2 covers invalid command options; status 78 covers launcher guard failures such as a temperature stop. Keep `RestartPreventExitStatus=2 78` in place.

Stop any foreground instance and wait for cleanup before deliberately starting the service:

```bash
systemctl --user start hypernoid-miner.service
systemctl --user status hypernoid-miner.service
journalctl --user -u hypernoid-miner.service -n 100 --no-pager
```

Check that startup uses your intended settings and that user-phase work is credited to your pool. If you decide to start it automatically with your user manager in future sessions:

```bash
systemctl --user enable hypernoid-miner.service
```

Enabling this user unit does not itself enable lingering after logout. Whether a user manager remains active depends on the host's login policy. Deliberately review that policy if you want the workload to continue without an active login.

## Stop, disable, or correct a failed start

```bash
systemctl --user stop hypernoid-miner.service
systemctl --user disable hypernoid-miner.service
```

Stopping ends the current run; disabling removes future automatic startup. To run again after fixing a guard failure, inspect the journal and correct the cause before manually starting it. If systemd's start limit was reached, use `systemctl --user reset-failed hypernoid-miner.service` after that correction.

Reload the user manager after editing the unit file. A settings-file-only change is read on the next service start. Use the complete version-switch procedure in [updating](../docs/updating.md); do not replace files inside a running release.
