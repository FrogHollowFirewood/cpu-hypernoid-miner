# Modified for CPU HyperNOID Miner v1.0.0: baseline-safe public builds and separate offline correctness target.
CC ?= gcc
CFLAGS ?= -O3 -Wall -Wextra
# Keep the compatibility artifact independent of the build host's vector ISA.
# Per-function attributes add only the runtime-checked SSSE3 and PCLMUL features.
BASELINE_FLAGS = -march=x86-64 -mtune=generic -mno-avx -mno-avx2 -mno-avx512f -mno-gfni -mno-vpclmulqdq
NOID_BATCH_GROUP ?= 4
TOWER_GROUPS ?= 2
CPPFLAGS += -DNOID_BATCH_GROUP=$(NOID_BATCH_GROUP) -DTOWER_GROUPS=$(TOWER_GROUPS)
.DELETE_ON_ERROR:
.PHONY: all check clean
all: noid-worker
noid_gf64_tables.h: generate_gf64_tables.py generate.py generate_aes_tables.py noid_tables.h
	python3 generate_gf64_tables.py > $@
noid-worker: worker.c noid.c noid.h noid_tables.h noid_gf64_tables.h Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread worker.c noid.c -o $@
test-targets: test_targets.c noid.c noid.h noid_tables.h noid_gf64_tables.h Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_targets.c noid.c -o $@
test-job-transitions: test_job_transitions.c noid.c noid.h noid_tables.h noid_gf64_tables.h Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_job_transitions.c noid.c -o $@
test-field: test_field.c noid.c noid.h noid_tables.h noid_gf64_tables.h Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_field.c -o $@
check: noid-worker test-targets test-job-transitions test-field
	./noid-worker --self-test
	python3 check_vectors.py
	NOID_TEST_WORKER="$(CURDIR)/noid-worker" python3 -m unittest -v test_noid_stratum
	python3 -m unittest -v test_worker
	./test-targets
	./test-job-transitions
	./test-field
	python3 -m unittest -v test_nonce_groups
	python3 check_isa.py ./noid-worker
clean:
	rm -f noid-worker test-targets test-job-transitions test-field

.PHONY: check-offline
check-offline: noid-worker test-targets test-job-transitions test-field
	./noid-worker --self-test
	python3 check_vectors.py
	python3 -m unittest -v test_worker
	./test-targets
	./test-job-transitions
	./test-field
	python3 -m unittest -v test_nonce_groups
	python3 check_isa.py ./noid-worker
