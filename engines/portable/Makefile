CC ?= cc
CFLAGS ?= -O3 -Wall -Wextra
TARGET_MACHINE := $(shell $(CC) -dumpmachine)
NOID_BATCH_GROUP ?= 1
CPPFLAGS += -DNOID_BATCH_GROUP=$(NOID_BATCH_GROUP)
BASELINE_FLAGS = -fno-tree-vectorize -fno-tree-slp-vectorize
ifneq (,$(findstring x86_64,$(TARGET_MACHINE)))
BASELINE_FLAGS += -march=x86-64 -mtune=generic -mno-sse3 -mno-ssse3 -mno-sse4.1 -mno-sse4.2 -mno-avx -mno-avx2 -mno-avx512f -mno-pclmul -mno-gfni -mno-bmi -mno-bmi2 -mno-aes
endif
ifneq (,$(findstring aarch64,$(TARGET_MACHINE)))
BASELINE_FLAGS += -march=armv8-a
endif

.DELETE_ON_ERROR:
.PHONY: all check check-offline clean
all: noid-worker
noid-worker: worker.c noid.c noid.h noid_tables.h Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread worker.c noid.c -o $@
test-portable: test_portable.c noid.c noid.h noid_tables.h Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_portable.c -o $@
test-targets: test_targets.c noid.c noid.h noid_tables.h noid_fixtures.h Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_targets.c noid.c -o $@
test-job-transitions: test_job_transitions.c noid.c noid.h noid_tables.h Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_job_transitions.c noid.c -o $@
check-offline: noid-worker test-portable test-targets test-job-transitions
	./test-portable
	python3 check_vectors.py
	./noid-worker --self-test
	./test-targets
	./test-job-transitions
	python3 -m unittest -v test_worker test_nonce_groups
check: check-offline
clean:
	rm -f noid-worker test-portable test-targets test-job-transitions
