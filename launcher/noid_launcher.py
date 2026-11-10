"""Linux foreground launcher for CPU HyperNOID Miner v1.0.0.

The user supplies their pool and wallet. The separate, disclosed fee policy
defines the fixed miner-use fee recipient. Package hashes detect inconsistent
files; they do not authenticate an untrusted distribution or its manifest.
"""
import argparse
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

VERSION = '1.0.0'
PRODUCT = 'CPU HyperNOID Miner'
ROOT = Path(__file__).resolve().parent.parent
ORDER = ('avx512_gfni', 'avx2_vpclmul256', 'avx2_pclmul_packed', 'ssse3_pclmul128', 'portable')
COMMON = ('hypernoid-miner', 'launcher/hypernoid-miner', 'launcher/noid_launcher.py',
          'launcher/noid_stratum.py', 'launcher/fee-policy.json', 'build/cpu-probe')
WALLET = re.compile(r'o1[023456789acdefghjklmnpqrstuvwxyz]{10,90}\Z')
WORKER = re.compile(r'[A-Za-z0-9_-]{1,32}\Z')
CPU_DRIVERS = {'coretemp', 'k10temp', 'k8temp', 'zenpower'}
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGTSTP)


class SafetyError(Exception):
    """An explicit configuration or runtime protection failed."""


def validate_fee_policy(value):
    expected = {'schema': 1, 'product': PRODUCT, 'package_version': VERSION, 'fee_percent': 3,
                'allocation': 'scheduled_monotonic_runtime', 'cycle_seconds': 6000,
                'user_seconds': 5820, 'fee_seconds': 180, 'fee_shutdown_reserve_seconds': 4,
                'first_phase': 'user', 'restart_policy': 'start_new_user_cycle',
                'missed_window_policy': 'skip_without_catchup',
                'fee_failure_policy': 'resume_user_and_skip_current_fee_window',
                'connection_and_shutdown_time_are_inside_allocation': True}
    if not isinstance(value, dict) or any(value.get(key) != item for key, item in expected.items()):
        raise SafetyError('Unrecognized miner-use fee schedule; expected the disclosed 97/3 runtime policy')
    pool = value.get('fee_pool', {})
    if not isinstance(pool, dict) or pool.get('tls') is not True or pool.get('trust') != 'default_certificate_store':
        raise SafetyError('The fee pool must use certificate-verified TLS with default trust')
    try: pool_address(str(pool.get('host', ''))+':'+str(pool.get('port', '')))
    except argparse.ArgumentTypeError as exc: raise SafetyError('Invalid fee pool in policy') from exc
    if not WALLET.fullmatch(value.get('fee_wallet', '')) or not WORKER.fullmatch(value.get('fee_worker', '')):
        raise SafetyError('Invalid public fee recipient in policy')
    return value


class FeeSchedule:
    """Absolute runtime slots; elapsed/missed fee slots are never made up."""
    def __init__(self, policy, started):
        self.policy = policy
        self.started = started
        self.last_time = started
        self.skipped_cycle = -1

    def phase(self, now):
        if now < self.last_time: raise SafetyError('Monotonic schedule clock moved backwards')
        self.last_time = now
        cycle = int((now-self.started)//self.policy['cycle_seconds'])
        begin = self.started+cycle*self.policy['cycle_seconds']
        fee_start = begin+self.policy['user_seconds']
        if now >= fee_start and cycle != self.skipped_cycle:
            return 'fee', begin+self.policy['cycle_seconds'], cycle
        if now >= fee_start:
            return 'user', begin+self.policy['cycle_seconds']+self.policy['user_seconds'], cycle
        return 'user', fee_start, cycle

    def skip_fee(self, cycle):
        self.skipped_cycle = cycle


def pool_address(text):
    try:
        value = urlsplit(text if '://' in text else 'stratum+ssl://'+text)
        if value.scheme not in ('stratum+ssl', 'stratum+tls', 'tls'):
            raise ValueError('only certificate-verified TLS pools are supported')
        host, port = value.hostname, value.port
        if not host or port is None or not 1 <= port <= 65535:
            raise ValueError('include a hostname and port, for example pool.example:19601')
        if value.username is not None or value.password is not None or value.path not in ('', '/') or value.query or value.fragment:
            raise ValueError('credentials, paths, queries, and fragments are not allowed in --pool')
        if len(host) > 253 or any(c.isspace() or ord(c) < 32 for c in host):
            raise ValueError('invalid pool hostname')
        return host, port
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def thread_count(text):
    try: value = int(text)
    except ValueError as exc: raise argparse.ArgumentTypeError('threads must be an integer') from exc
    if not 1 <= value <= 1024:
        raise argparse.ArgumentTypeError('threads must be between 1 and 1024, independently of backend')
    return value


def stop_temperature(text):
    try: value = float(text)
    except ValueError as exc: raise argparse.ArgumentTypeError('temperature must be a Celsius number') from exc
    if not math.isfinite(value) or not 0 < value <= 150:
        raise argparse.ArgumentTypeError('temperature must be finite and between 0 and 150 Celsius; choose a suitable hardware limit')
    return value


class Package:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve(strict=True)
        path = self.root/'build/runtime-manifest.json'
        try:
            if path.stat().st_size > 2*1024*1024: raise SafetyError('Runtime manifest is too large')
            self.manifest = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise SafetyError('Missing or invalid build/runtime-manifest.json; build the package first') from exc
        if self.manifest.get('schema') != 1 or self.manifest.get('package_version') != VERSION:
            raise SafetyError('Runtime manifest has a different schema or package version')
        self.files = self.manifest.get('files')
        if not isinstance(self.files, dict): raise SafetyError('Runtime manifest has no file checksums')
        for name, value in self.files.items():
            rel = Path(name)
            if not name or rel.is_absolute() or '..' in rel.parts or not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
                raise SafetyError('Invalid runtime manifest entry')
        for name in COMMON: self.verify(name)

    def verify(self, name):
        if name not in self.files: raise SafetyError('Runtime checksum is missing: '+name)
        path = self.root/name
        try:
            if path.resolve(strict=True) != path or not path.is_file():
                raise SafetyError('Runtime file must be a regular file inside this package: '+name)
            with path.open('rb') as stream:
                value = hashlib.sha256()
                while block := stream.read(1024*1024): value.update(block)
            if value.hexdigest() != self.files[name]: raise SafetyError('Runtime checksum mismatch: '+name)
        except OSError as exc:
            raise SafetyError('Cannot read runtime file: '+name) from exc
        return path

    def probe(self):
        path = self.verify('build/cpu-probe')
        try:
            result = subprocess.run([str(path)], capture_output=True, text=True, timeout=15)
            if result.returncode or len(result.stdout) > 1024*1024:
                raise SafetyError('CPU/OS capability probe failed; refusing to guess a backend')
            data = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise SafetyError('CPU/OS probe could not run; build this package for the current machine') from exc
        if not isinstance(data, dict): raise SafetyError('CPU/OS probe must return a JSON object')
        supported = data.get('supported_backends')
        if data.get('schema') != 1 or not isinstance(supported, list) or any(name not in ORDER for name in supported):
            raise SafetyError('Unrecognized CPU/OS probe result')
        if data.get('preferred_backend') is not None and data['preferred_backend'] not in supported:
            raise SafetyError('CPU probe preference conflicts with its supported set')
        return data

    def fee_policy(self):
        try: value = json.loads(self.verify('launcher/fee-policy.json').read_text())
        except ValueError as exc: raise SafetyError('Invalid fee policy JSON') from exc
        return validate_fee_policy(value)

    def backends(self, probe):
        result = []
        for name in ORDER:
            relative = 'engines/'+name+'/noid-worker'
            available = relative in self.files and (self.root/relative).is_file()
            if available: self.verify(relative)
            metadata = self.manifest.get('backends', {}).get(name, {})
            result.append({'id': name, 'supported': name in probe['supported_backends'],
                           'installed': available, 'path': relative,
                           'engine_version': metadata.get('engine_version', 'See engine provenance'),
                           'provenance': metadata.get('provenance', 'See package source provenance')})
        return result


def select_backend(rows, requested):
    if requested != 'auto':
        item = next((item for item in rows if item['id'] == requested), None)
        if item is None or not item['supported']:
            raise SafetyError('Requested backend is not supported by every allowed CPU and its OS state: '+requested)
        if not item['installed']: raise SafetyError('Requested backend has not been built: '+requested)
        return item
    for item in rows:
        if item['supported'] and item['installed']: return item
    raise SafetyError('No supported, built backend is available; build the portable backend for this machine')


def probed_affinity(probe):
    cpus = probe.get('cpu_ids')
    if not isinstance(cpus, list) or not cpus or any(type(cpu) is not int or cpu < 0 for cpu in cpus) or sorted(set(cpus)) != cpus:
        raise SafetyError('CPU probe did not provide its exact allowed CPU set')
    if set(cpus) != os.sched_getaffinity(0):
        raise SafetyError('CPU affinity changed after capability detection; retry before starting work')
    return tuple(cpus)


def explicit_sensor(path):
    path = Path(path)
    try: resolved = path.resolve(strict=True)
    except OSError as exc: raise SafetyError('Temperature input does not exist: '+str(path)) from exc
    if not resolved.is_relative_to(Path('/sys/devices')) or not re.fullmatch(r'temp\d+_input', resolved.name):
        raise SafetyError('An explicit temperature file must be a Linux hwmon tempN_input under /sys/devices')
    return resolved


def discover_sensors():
    paths = set()
    for hw in Path('/sys/class/hwmon').glob('hwmon*'):
        try:
            if (hw/'name').read_text().strip() in CPU_DRIVERS:
                paths.update(path.resolve(strict=True) for path in hw.glob('temp*_input'))
        except OSError as exc:
            raise SafetyError('CPU temperature discovery failed: '+str(exc)) from exc
    return sorted(paths)


class TemperatureMonitor:
    def __init__(self, paths, cutoff):
        self.paths = tuple(paths)
        self.cutoff = cutoff
        if not self.paths: raise SafetyError('No supported CPU sensor found; select --temperature-file or explicitly choose --no-temperature-guard')

    def read(self):
        values = []
        for path in self.paths:
            try: value = int(path.read_text().strip())/1000
            except (OSError, ValueError) as exc: raise SafetyError('Temperature input failed: '+str(path)) from exc
            if not -20 < value < 150: raise SafetyError('Implausible CPU temperature from '+str(path))
            values.append(value)
        peak = max(values)
        if peak >= self.cutoff:
            raise SafetyError('Temperature cutoff reached: %.3f C (configured %.3f C)' % (peak, self.cutoff))
        return peak


def temperature_monitor(args):
    if args.no_temperature_guard: return None
    if args.stop_temperature is None: return None
    paths = [explicit_sensor(path) for path in args.temperature_file] if args.temperature_file else discover_sensors()
    monitor = TemperatureMonitor(paths, args.stop_temperature)
    monitor.read()
    return monitor


@contextmanager
def user_lock():
    uid = os.geteuid()
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime:
        base = Path(runtime)
        if not base.is_absolute() or base.resolve(strict=True) != base:
            raise SafetyError('XDG_RUNTIME_DIR must be an absolute directory without redirects')
        value = base.stat()
        if not stat.S_ISDIR(value.st_mode) or value.st_uid != uid or value.st_mode & 0o022:
            raise SafetyError('XDG_RUNTIME_DIR must be owned by this user and not writable by other users')
        directory = base/'hypernoid-miner'
    else:
        directory = Path('/tmp')/('hypernoid-miner-'+str(uid))
    if not directory.is_absolute(): raise SafetyError('The per-user runtime directory must be absolute')
    try: directory.mkdir(mode=0o700)
    except FileExistsError: pass
    if directory.resolve(strict=True) != directory:
        raise SafetyError('The per-user coordination directory must not redirect')
    for parent in directory.parents:
        entry = parent.stat()
        sticky = entry.st_uid in (0, uid) and stat.S_ISDIR(entry.st_mode) and entry.st_mode & stat.S_ISVTX
        if entry.st_uid not in (0, uid) or (entry.st_mode & 0o022 and not sticky):
            raise SafetyError('The per-user coordination directory has an unsafe parent')
    value = directory.stat()
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != uid or value.st_mode & 0o077:
        raise SafetyError('The per-user coordination directory must be owned by the current user with mode 0700')
    fd = os.open(directory/'hypernoid-miner.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        value = os.fstat(fd)
        if not stat.S_ISREG(value.st_mode) or value.st_uid != uid or value.st_mode & 0o077:
            raise SafetyError('The per-user lock is not a private regular file')
        try: fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc: raise SafetyError('Another HyperNOID instance holds this user\'s coordination lock') from exc
        yield
    finally: os.close(fd)


def require_process_handles():
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise SafetyError('PID-stable cleanup requires Python 3.9+ and Linux pidfd support')
    try: fd = os.pidfd_open(os.getpid())
    except OSError as exc: raise SafetyError('This Linux kernel does not provide the required pidfd process handles') from exc
    os.close(fd)


def child_setup(parent, cpu_ids=None):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong]*4
    libc.prctl.restype = ctypes.c_int
    def configure():
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        if cpu_ids is not None: os.sched_setaffinity(0, cpu_ids)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != parent: os._exit(125)
    return configure


def owned_processes(session):
    result = []
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            value = path.read_text(errors='replace').rsplit(')', 1)[1].split()
            if value[0] not in ('Z', 'X') and int(value[3]) == session:
                result.append(int(path.parent.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError): pass
    return result


def cleanup(child, monitor=None, graceful=True, grace_deadline=None, requested=None):
    old = {number: signal.getsignal(number) for number in STOP_SIGNALS}
    def defer(number, frame):
        if requested is not None: requested[0] = number
    for number in STOP_SIGNALS: signal.signal(number, defer)
    failure = None
    try:
        if graceful and child.poll() is None:
            child.send_signal(signal.SIGINT)
            deadline = time.monotonic()+4
            if grace_deadline is not None: deadline = min(deadline, grace_deadline)
            while child.poll() is None and time.monotonic() < deadline:
                try:
                    if monitor: monitor.read()
                except SafetyError as exc:
                    failure = exc; break
                time.sleep(.05)
        deadline = time.monotonic()+3
        while True:
            pids = owned_processes(child.pid)
            if not pids: break
            for pid in pids:
                try:
                    fd = os.pidfd_open(pid)
                    try:
                        if os.getsid(pid) == child.pid:
                            signal.pidfd_send_signal(fd, signal.SIGKILL)
                    finally: os.close(fd)
                except ProcessLookupError: pass
            if time.monotonic() >= deadline: raise SafetyError('An owned child did not stop within the cleanup deadline')
            time.sleep(.025)
        child.wait(timeout=3)
        if failure: raise failure
    finally:
        for number, handler in old.items(): signal.signal(number, handler)


def supervise(command, monitor, requested, timeout=None, cpu_ids=None,
              switch_deadline=None, shutdown_reserve=0, prelaunch_check=None):
    child = None
    failure = None
    result = 0
    try:
        if monitor: monitor.read()
        if requested[0] is not None: return 0
        if prelaunch_check: prelaunch_check()
        if switch_deadline is not None and time.monotonic() >= switch_deadline-shutdown_reserve:
            return None  # A delayed transition must not start an expired allocation.
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True,
                                 preexec_fn=child_setup(os.getpid(), cpu_ids))
        deadline = time.monotonic()+timeout if timeout else None
        while True:
            if monitor: monitor.read()
            if requested[0] is not None: break
            if switch_deadline is not None and time.monotonic() >= switch_deadline-shutdown_reserve:
                result = None  # Planned allocation change, not a pool/engine failure.
                break
            code = child.poll()
            if code is not None:
                result = 0 if code == 0 else 1
                break
            if deadline and time.monotonic() >= deadline:
                raise SafetyError('The bounded offline self-test exceeded its deadline')
            time.sleep(.1)
    except BaseException as exc:
        failure = exc
    finally:
        if child is not None:
            try: cleanup(child, monitor, graceful=failure is None,
                         grace_deadline=switch_deadline if shutdown_reserve else None,
                         requested=requested)
            except BaseException as exc:
                if failure is None: failure = exc
    if failure: raise failure
    return result


def phase_command(args, client, engine, policy, phase):
    fee = phase == 'fee'
    host, port = (policy['fee_pool']['host'], policy['fee_pool']['port']) if fee else args.pool
    wallet = policy['fee_wallet'] if fee else args.wallet
    worker = policy['fee_worker'] if fee else args.worker
    command = [sys.executable, '-I', '-B', '-u', str(client), '--host', host, '--port', str(port),
               '--payout-address', wallet, '--worker', worker, '--accounting', phase]
    if not fee and args.ca_file: command += ['--ca-file', str(Path(args.ca_file).resolve(strict=True))]
    if fee or args.once: command += ['--once']
    return command+['--engine', str(engine), '--worker', str(args.threads)]


def scheduled_mining(args, client, engine, policy, monitor, requested, probe, phase_check, clock=None):
    clock = clock or time.monotonic
    schedule = FeeSchedule(policy, clock())
    while requested[0] is None:
        now = clock()
        phase, deadline, cycle = schedule.phase(now)
        reserve = policy['fee_shutdown_reserve_seconds'] if phase == 'fee' else 0
        if phase == 'fee' and deadline-now <= reserve:
            schedule.skip_fee(cycle)
            continue
        print('Allocation %s | %.1f scheduled seconds remain | fresh connection and worker; counters reset.' %
              (phase.upper(), deadline-now), flush=True)
        command = phase_command(args, client, engine, policy, phase)
        outcome = supervise(command, monitor, requested, cpu_ids=probed_affinity(probe),
                            switch_deadline=deadline, shutdown_reserve=reserve, prelaunch_check=phase_check)
        if requested[0] is not None: return 0
        if phase == 'fee':
            schedule.skip_fee(cycle)
            if outcome is not None:
                print('FEE session ended; returning to USER allocation. No retry or catchup in this fee window.', flush=True)
        elif outcome is not None:
            return outcome  # Includes the user's explicit --once behavior.
    return 0


def parser():
    try: available_threads = len(os.sched_getaffinity(0))
    except (AttributeError, OSError): available_threads = os.cpu_count() or 1
    value = argparse.ArgumentParser(description=PRODUCT+' v'+VERSION+' — Linux foreground Parano1d CPU miner',
        epilog='Includes a disclosed 3% scheduled-runtime miner-use fee: 97 minutes your pool, then up to 3 minutes the fixed fee pool per 100-minute cycle. Starts with you; no catchup. Connection/shutdown time is inside each slot, not a 3% share or revenue guarantee. See --check and launcher/fee-policy.json for the exact recipient.')
    action = value.add_mutually_exclusive_group()
    action.add_argument('--version', action='version', version=PRODUCT+' v'+VERSION+' | 3% scheduled-runtime miner-use fee')
    action.add_argument('--list-backends', action='store_true', help='list CPU/OS support, built engines, and provenance; no mining')
    action.add_argument('--check', action='store_true', help='check package, selected backend, configured sensor, and user lock; no network/mining')
    action.add_argument('--self-test', action='store_true', help='run the selected engine offline, bounded to 120 seconds; no pool')
    value.add_argument('--backend', choices=('auto',)+ORDER, default='auto')
    value.add_argument('--pool', type=pool_address, metavar='HOST:PORT', help='certificate-verified TLS pool; optional stratum+ssl:// prefix')
    value.add_argument('--wallet', help='your public NOID receiving address (o1...)')
    value.add_argument('--worker', default='HyperNOID', help='pool worker label, 1–32 letters/digits/underscores/hyphens; default HyperNOID')
    value.add_argument('--threads', type=thread_count, default=max(1,min(1024,available_threads)), help='CPU worker threads, 1–1024; default allowed CPU affinity count, independent of backend')
    value.add_argument('--ca-file', help='optional PEM trust file; certificate and hostname verification stay enabled')
    value.add_argument('--once', action='store_true', help='exit after the first disconnected pool session')
    thermal = value.add_mutually_exclusive_group()
    thermal.add_argument('--stop-temperature', type=stop_temperature, metavar='C', help='your hardware-specific cutoff in Celsius; no universal default')
    thermal.add_argument('--no-temperature-guard', action='store_true', help='explicitly run without software temperature monitoring')
    value.add_argument('--temperature-file', action='append', default=[], metavar='/sys/.../tempN_input', help='explicit CPU hwmon input; repeat to monitor the maximum; otherwise auto-detect CPU sensors')
    return value


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    mining = not (args.list_backends or args.check or args.self_test)
    if args.wallet is not None and not WALLET.fullmatch(args.wallet): arguments.error('--wallet must be a public NOID o1 receiving address')
    if args.worker is not None and not WORKER.fullmatch(args.worker): arguments.error('--worker must use 1–32 letters, digits, underscores, or hyphens')
    if args.temperature_file and args.stop_temperature is None: arguments.error('--temperature-file requires --stop-temperature')
    if mining:
        for name in ('pool', 'wallet'):
            if getattr(args, name) is None: arguments.error('mining requires --'+name.replace('_', '-'))
        if args.stop_temperature is None and not args.no_temperature_guard:
            arguments.error('choose --stop-temperature for your CPU or explicitly --no-temperature-guard')
    if platform.system() != 'Linux':
        print('STOPPED: This release supports Linux only.', file=sys.stderr); return 78
    requested = [None]
    old = {number: signal.getsignal(number) for number in STOP_SIGNALS}
    def stop(number, frame): requested[0] = number
    for number in STOP_SIGNALS: signal.signal(number, stop)
    try:
        package = Package()
        policy = package.fee_policy()
        probe = package.probe()
        cpu_ids = probed_affinity(probe)
        rows = package.backends(probe)
        if args.list_backends:
            print(PRODUCT+' v'+VERSION+' | architecture: '+str(probe.get('architecture', 'unknown')))
            for item in rows:
                print('%-22s supported=%-5s built=%-5s | %s | provenance=%s' %
                      (item['id'], item['supported'], item['installed'], item['engine_version'], item['provenance']))
            return 0
        selected = select_backend(rows, args.backend)
        engine = package.verify(selected['path'])
        require_process_handles()
        monitor = temperature_monitor(args)
        with user_lock():
            if args.check:
                print(json.dumps({'product': PRODUCT, 'version': VERSION, 'ready': True,
                    'backend': selected['id'], 'architecture': probe.get('architecture'),
                    'threads': args.threads, 'affinity_cpus': len(os.sched_getaffinity(0)),
                    'temperature_guard': bool(monitor), 'stop_temperature_c': args.stop_temperature,
                    'temperature_inputs': [str(p) for p in monitor.paths] if monitor else [],
                    'mining_configuration_complete': all(getattr(args, name) is not None for name in ('pool','wallet','worker','threads')) and (bool(monitor) or args.no_temperature_guard),
                    'miner_use_fee': policy,
                    'network_started': False, 'worker_started': False}, indent=2))
                return 0
            print(PRODUCT+' v'+VERSION+' | selected backend: '+selected['id']+
                  (' | offline self-test' if args.self_test else ' | threads: '+str(args.threads)), flush=True)
            if monitor:
                print('Temperature guard: maximum of %d CPU input(s), cutoff %.3f C.' % (len(monitor.paths), monitor.cutoff), flush=True)
            elif mining:
                print('Temperature guard: disabled by explicit --no-temperature-guard.', flush=True)
            if args.self_test:
                return supervise([str(engine), '--self-test'], monitor, requested, timeout=120, cpu_ids=probed_affinity(probe))
            client = package.verify('launcher/noid_stratum.py')
            print('Miner-use fee: 3% scheduled runtime — 97 minutes USER, then up to 3 minutes FEE per 100 minutes; no catchup.', flush=True)
            print('Fee recipient: %s | TLS %s:%d | worker %s | default certificate trust.' %
                  (policy['fee_wallet'], policy['fee_pool']['host'], policy['fee_pool']['port'], policy['fee_worker']), flush=True)
            print('Connection and shutdown delays are inside the slots; fee cleanup begins up to 4 seconds early. This is not a share/revenue percentage guarantee.', flush=True)
            def phase_check():
                for name in ('launcher/noid_stratum.py', selected['path'], 'launcher/fee-policy.json'):
                    package.verify(name)
            return scheduled_mining(args, client, engine, policy, monitor, requested, probe, phase_check)
    except (SafetyError, OSError, ValueError) as exc:
        print('STOPPED: '+str(exc), file=sys.stderr, flush=True)
        return 78
    finally:
        for number, handler in old.items(): signal.signal(number, handler)
