# SPDX-License-Identifier: Apache-2.0
# CPU HyperNOID Miner v1.0.0 public build interface.
PYTHON ?= python3
CC ?= cc
CFLAGS ?= -O3 -Wall -Wextra
ARCH ?= $(shell uname -m)
BACKEND ?= auto
export CC CFLAGS

.PHONY: all probe backends manifest check check-supported check-probe clean
all: probe backends
	$(PYTHON) engines/build_public.py manifest --arch "$(ARCH)"
probe:
	$(PYTHON) engines/build_public.py probe --arch "$(ARCH)"
backends:
	$(PYTHON) engines/build_public.py backends --arch "$(ARCH)"
manifest:
	$(PYTHON) engines/build_public.py manifest --arch "$(ARCH)"
check: probe backends
	$(PYTHON) engines/build_public.py check --arch "$(ARCH)" --backend "$(BACKEND)"
check-supported: probe backends
	$(PYTHON) engines/build_public.py check-supported --arch "$(ARCH)"
check-probe: probe
	$(PYTHON) engines/build_public.py check-probe --arch "$(ARCH)"
clean:
	$(PYTHON) -c 'from pathlib import Path; import subprocess; [subprocess.run(["make","-C",str(p.parent),"clean"],check=True) for p in Path("engines").glob("*/Makefile")]'
	$(PYTHON) -c 'from pathlib import Path; [p.unlink(missing_ok=True) for p in map(Path,["build/cpu-probe","build/test-cpu-probe","build/runtime-manifest.json","build/build-info.json"])]'
