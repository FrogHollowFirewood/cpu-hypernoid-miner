#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Package an already built and validated native Linux release; never mine."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.0.0"


def clean_commit():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT, check=True)
    other = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    if other:
        raise RuntimeError("Untracked source files exist; review and commit or remove them before qualifying a release")
    return commit


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_file(relative):
    path = ROOT / relative
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
        raise RuntimeError("Missing or redirected package file: " + relative)
    return path.read_bytes()


def tree_files(directory, tracked):
    # Only reviewed committed documentation can enter an archive. In particular,
    # ignored local logs, .env files, keys and notes must never be swept up.
    yield from sorted(name for name in tracked if name.startswith(directory + "/"))


def archive(destination, prefix, files, epoch):
    # Stable ownership, order, modes, gzip header and timestamps. The compiler's
    # output itself is identified by hashes; this does not claim universal
    # bit-for-bit reproducibility across different compilers.
    sums = "".join(f"{sha(data)}  {name}\n" for name, (data, _) in sorted(files.items()))
    files = dict(files)
    files["PACKAGE-SHA256SUMS"] = (sums.encode(), 0o644)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for name, (data, mode) in sorted(files.items()):
                    info = tarfile.TarInfo(prefix + "/" + name)
                    info.size = len(data)
                    info.mode = mode
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = epoch
                    tar.addfile(info, io.BytesIO(data))
    return {"file": destination.name, "sha256": sha(destination.read_bytes()), "bytes": destination.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--source", action="store_true", help="also package tracked source files from the current Git tree")
    args = parser.parse_args()
    source_commit = clean_commit()
    tracked = set(filter(None, subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "-z", source_commit], cwd=ROOT).decode().split("\0")))
    manifest_bytes = checked_file("build/runtime-manifest.json")
    manifest = json.loads(manifest_bytes)
    info = json.loads(checked_file("build/build-info.json"))
    arch = manifest.get("architecture")
    if arch not in {"x86_64", "aarch64"} or arch != platform.machine():
        raise RuntimeError("Release packaging requires a native x86_64 or aarch64 build")
    if manifest.get("package_version") != VERSION or info.get("package_version") != VERSION or info.get("architecture") != arch:
        raise RuntimeError("Mismatched release version")
    receipt = json.loads(checked_file("build/validation/ci-validation.json"))
    expected_phases = {"probe", "engines", "launcher", "public-check", "public-self-test"}
    phases = receipt.get("phases", [])
    if (receipt.get("version") != VERSION or receipt.get("architecture") != arch
            or receipt.get("native") is not True or receipt.get("source_commit") != source_commit
            or receipt.get("runtime_manifest_sha256") != sha(manifest_bytes)
            or len(phases) != len(expected_phases)
            or {phase.get("name") for phase in phases} != expected_phases
            or any(phase.get("exit_code") != 0 for phase in phases)):
        raise RuntimeError("Release validation receipt does not match this commit, architecture and runtime manifest")
    expected_backends = {"portable"} if arch == "aarch64" else {
        "avx512_gfni", "avx2_vpclmul256", "avx2_pclmul_packed", "ssse3_pclmul128", "portable"}
    if set(manifest.get("backends", {})) != expected_backends:
        raise RuntimeError("Architecture package is missing a required backend")
    required = {"hypernoid-miner", "build/cpu-probe", "launcher/hypernoid-miner", "launcher/noid_launcher.py", "launcher/noid_stratum.py", "launcher/fee-policy.json"}
    required |= {"engines/" + name + "/noid-worker" for name in expected_backends}
    if set(manifest.get("files", {})) != required:
        raise RuntimeError("Unexpected runtime manifest file set")
    files = {}
    executable = {"hypernoid-miner", "launcher/hypernoid-miner", "build/cpu-probe"}
    executable |= {"engines/" + name + "/noid-worker" for name in expected_backends}
    for name, expected in manifest["files"].items():
        if name in {"hypernoid-miner", "launcher/hypernoid-miner", "launcher/noid_launcher.py", "launcher/noid_stratum.py", "launcher/fee-policy.json"} and name not in tracked:
            raise RuntimeError("Runtime source is not part of the validated commit: " + name)
        data = checked_file(name)
        if sha(data) != expected:
            raise RuntimeError("Runtime checksum mismatch: " + name)
        files[name] = (data, 0o755 if name in executable else 0o644)
    extras = {"hypernoid-miner", "README.md", "LICENSE", "NOTICE", "CHANGELOG.md", "SECURITY.md",
              "build/runtime-manifest.json", "build/build-info.json", "engines/PROVENANCE.json",
              "engines/SOURCE-SHA256SUMS", "launcher/VALIDATION.json"}
    for name in expected_backends:
        extras.update({"engines/" + name + "/LICENSE", "engines/" + name + "/NOTICE"})
        for provenance in ("PROVENANCE.json", "SOURCE-PROVENANCE.json"):
            candidate = "engines/" + name + "/" + provenance
            if (ROOT / candidate).is_file():
                extras.add(candidate)
    for directory in ("docs", "examples", "validation"):
        extras.update(tree_files(directory, tracked))
    extras.add("build/validation/ci-validation.json")
    for name in sorted(extras):
        if not name.startswith("build/") and name not in tracked:
            raise RuntimeError("Release source/documentation is not tracked: " + name)
        files[name] = (checked_file(name), 0o755 if name in executable else 0o644)
    args.output.mkdir(parents=True, exist_ok=True)
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    prefix = f"cpu-hypernoid-miner-v{VERSION}-linux-{arch}"
    assets = [archive(args.output / (prefix + ".tar.gz"), prefix, files, epoch)]
    if args.source:
        source = {}
        committed = subprocess.check_output(["git", "archive", "--format=tar", source_commit], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(committed), mode="r:") as tree:
            for member in tree.getmembers():
                if member.isdir():
                    continue
                name = member.name
                if not member.isfile() or Path(name).is_absolute() or ".." in Path(name).parts:
                    raise RuntimeError("Only regular files are allowed in the source release: " + name)
                if name.startswith(("build/", "dist/")) or "__pycache__" in Path(name).parts:
                    raise RuntimeError("Generated/private output is unexpectedly tracked: " + name)
                data = tree.extractfile(member).read()
                if data.startswith(b"\x7fELF"):
                    raise RuntimeError("Compiled binary unexpectedly tracked: " + name)
                mode = 0o755 if member.mode & 0o111 else 0o644
                source[name] = (data, mode)
        if not source:
            raise RuntimeError("No tracked source files found")
        source_prefix = f"cpu-hypernoid-miner-v{VERSION}-source"
        assets.append(archive(args.output / (source_prefix + ".tar.gz"), source_prefix, source, epoch))
    checksum_path = args.output / ("SHA256SUMS-" + arch)
    checksum_path.write_text("".join(f"{item['sha256']}  {item['file']}\n" for item in assets))
    print(json.dumps({"architecture": arch, "assets": assets, "checksums": checksum_path.name}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit("Packaging failed: " + str(exc))
