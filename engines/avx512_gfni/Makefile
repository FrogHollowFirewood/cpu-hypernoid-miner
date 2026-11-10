# Modified for CPU HyperNOID Miner v1.0.0: baseline-safe public builds and separate offline correctness target.
CC ?= gcc
CFLAGS ?= -O3 -Wall -Wextra
BASELINE_FLAGS = -march=x86-64 -mtune=generic -mno-avx -mno-avx2 -mno-avx512f -mno-gfni -mno-vpclmulqdq
NOID_BATCH_GROUP ?= 4
CPPFLAGS += -DNOID_BATCH_GROUP=$(NOID_BATCH_GROUP)

.DELETE_ON_ERROR:
.PHONY: all check clean

all: noid-worker

noid-seventh.S: generate_seventh.py generate.py generate_aes_tables.py noid_tables.h noid_aes_tables.h
	python3 $< > $@

noid-worker: worker.c noid.c noid.h noid_fused_keys.h first_polynomial.inc noid_tables.h noid_aes_tables.h noid-seventh.S Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread worker.c noid.c noid-seventh.S -o $@

test-trace16: test_trace16.c noid.c noid.h noid_fused_keys.h first_polynomial.inc noid_aes_tables.h noid_tables.h noid-seventh.S Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_trace16.c noid.c noid-seventh.S -o $@

test-trace16-scan: test_trace16_scan.c noid.c noid.h noid_fused_keys.h first_polynomial.inc noid_aes_tables.h noid_tables.h noid-seventh.S Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_trace16_scan.c noid.c noid-seventh.S -o $@

test-first-polynomial: test_first_polynomial.c noid.c noid.h noid_fused_keys.h first_polynomial.inc noid_tables.h noid_aes_tables.h noid-seventh.S Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_first_polynomial.c noid-seventh.S -o $@

test-first-cache: test_first_cache.c noid.c noid.h noid_fused_keys.h first_polynomial.inc noid_tables.h noid_aes_tables.h noid-seventh.S Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_first_cache.c noid.c noid-seventh.S -o $@

test-polynomial-field: test_polynomial_field.c noid.c noid.h noid_fused_keys.h first_polynomial.inc noid_tables.h noid_aes_tables.h noid-seventh.S Makefile
	$(CC) $(CPPFLAGS) $(CFLAGS) $(BASELINE_FLAGS) -std=c11 -pthread test_polynomial_field.c noid-seventh.S -o $@

check: noid-worker test-trace16 test-trace16-scan test-first-polynomial test-first-cache test-polynomial-field
	./noid-worker --self-test
	python3 check_vectors.py
	NOID_TEST_WORKER="$(CURDIR)/noid-worker" python3 -m unittest -v test_noid_stratum
	python3 -m unittest -v test_worker
	./test-trace16
	./test-trace16-scan
	./test-first-polynomial
	./test-first-cache
	./test-polynomial-field
	python3 -m unittest -v test_nonce_groups
	CC="$(CC)" python3 check_sparse_transform.py

clean:
	rm -f noid-worker noid-seventh.S noid_fused_keys.h test-trace16 test-trace16-scan test-first-polynomial test-first-cache test-polynomial-field

noid_fused_keys.h: generate_seventh.py noid_tables.h noid_aes_tables.h
	python3 $< --fused-keys > $@

.PHONY: check-offline
check-offline: noid-worker test-trace16 test-trace16-scan test-first-polynomial test-first-cache test-polynomial-field
	./noid-worker --self-test
	python3 check_vectors.py
	python3 -m unittest -v test_worker
	./test-trace16
	./test-trace16-scan
	./test-first-polynomial
	./test-first-cache
	./test-polynomial-field
	python3 -m unittest -v test_nonce_groups
	CC="$(CC)" python3 check_sparse_transform.py

