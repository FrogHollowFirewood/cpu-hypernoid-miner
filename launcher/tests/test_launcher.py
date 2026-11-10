import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import noid_launcher as launcher
import noid_stratum as client

ENTRY = Path(__file__).resolve().parents[1]/'hypernoid-miner'


class ArgumentsAndDispatch(unittest.TestCase):
    def test_help_and_version_do_not_need_built_engines(self):
        for flag in ('--help', '--version'):
            result = subprocess.run([sys.executable, '-B', str(ENTRY), flag], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertIn('CPU HyperNOID Miner', result.stdout)
            self.assertIn('3%', result.stdout)

    def test_bad_arguments_and_explicit_thermal_policy(self):
        base = ['--pool', 'pool.example:19601', '--wallet', 'o1'+'q'*20, '--worker', 'test', '--threads', '24']
        cases = [[], base, base+['--stop-temperature', 'nan'], base+['--stop-temperature', '0'],
                 ['--threads', '0'], ['--threads', '1025'], ['--pool', 'stratum+tcp://pool.example:19601'],
                 ['--pool', 'tls://user:secret@pool.example:19601'], ['--pool', 'pool.example'],
                 ['--wallet', 'not-a-wallet'], ['--worker', 'bad.worker'],
                 ['--temperature-file', '/tmp/fake']]
        for argv in cases:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped: launcher.main(argv)
                self.assertEqual(stopped.exception.code, 2)

    def test_tls_pool_parsing_and_thread_independence(self):
        self.assertEqual(launcher.pool_address('[::1]:19601'), ('::1', 19601))
        self.assertEqual(launcher.pool_address('stratum+ssl://pool.example:19601'), ('pool.example', 19601))
        args = launcher.parser().parse_args(['--backend', 'portable', '--threads', '32'])
        self.assertEqual((args.backend, args.threads), ('portable', 32))

    def test_auto_selects_only_supported_installed_engines(self):
        rows = [{'id': name, 'supported': name in ('avx2_pclmul_packed', 'portable'),
                 'installed': name != 'avx2_pclmul_packed'} for name in launcher.ORDER]
        self.assertEqual(launcher.select_backend(rows, 'auto')['id'], 'portable')
        with self.assertRaises(launcher.SafetyError): launcher.select_backend(rows, 'avx512_gfni')
        with self.assertRaises(launcher.SafetyError): launcher.select_backend(rows, 'avx2_pclmul_packed')

    def test_probe_affinity_must_match_current_allowed_cpus(self):
        with patch.object(os, 'sched_getaffinity', return_value={1, 3}):
            self.assertEqual(launcher.probed_affinity({'cpu_ids': [1, 3]}), (1, 3))
            for cpus in ([1], [1, 3, 4], [3, 1], [1, 1], []):
                with self.assertRaises(launcher.SafetyError): launcher.probed_affinity({'cpu_ids': cpus})

    def test_package_checksum_and_path_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'build').mkdir()
            values = {}
            for name in launcher.COMMON:
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'test file\n')
                values[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = {'schema': 1, 'package_version': launcher.VERSION, 'files': values}
            target = root/'build/runtime-manifest.json'; target.write_text(json.dumps(manifest))
            launcher.Package(root)
            (root/'launcher/noid_stratum.py').write_text('changed')
            with self.assertRaises(launcher.SafetyError): launcher.Package(root)
            manifest['files']['../escape'] = '0'*64; target.write_text(json.dumps(manifest))
            with self.assertRaises(launcher.SafetyError): launcher.Package(root)

    def test_private_lock_refuses_second_instance(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'XDG_RUNTIME_DIR': folder}):
            with launcher.user_lock():
                with self.assertRaises(launcher.SafetyError):
                    with launcher.user_lock(): pass

    def test_cli_check_and_offline_self_test_with_stub_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'launcher').mkdir(); (root/'build').mkdir()
            (root/'engines/portable').mkdir(parents=True); (root/'locks').mkdir(mode=0o700)
            for name in ('hypernoid-miner', 'noid_launcher.py', 'noid_stratum.py', 'fee-policy.json'):
                shutil.copyfile(ENTRY.parent/name, root/'launcher'/name)
            shutil.copyfile(ENTRY.parent.parent/'hypernoid-miner', root/'hypernoid-miner')
            probe = {'schema': 1, 'architecture': 'test-fixture', 'supported_backends': ['portable'],
                     'preferred_backend': 'portable', 'cpu_ids': sorted(os.sched_getaffinity(0))}
            (root/'build/cpu-probe').write_text('#!'+sys.executable+'\nprint('+repr(json.dumps(probe))+')\n')
            marker = root/'worker-was-started'
            (root/'engines/portable/noid-worker').write_text('#!'+sys.executable+'\nfrom pathlib import Path\nimport sys\n'
                'assert sys.argv[1:]==["--self-test"]\nPath('+repr(str(marker))+').write_text("offline stub only")\n')
            for name in ('build/cpu-probe', 'engines/portable/noid-worker'): (root/name).chmod(0o755)
            names = list(launcher.COMMON)+['engines/portable/noid-worker']
            manifest = {'schema': 1, 'package_version': launcher.VERSION,
                        'files': {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}}
            (root/'build/runtime-manifest.json').write_text(json.dumps(manifest))
            environment = dict(os.environ, XDG_RUNTIME_DIR=str(root/'locks'))
            command = [sys.executable, '-B', str(root/'launcher/hypernoid-miner')]
            checked = subprocess.run(command+['--check', '--pool', 'localhost:1', '--wallet', 'o1'+'q'*20,
                '--no-temperature-guard'], env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(checked.stdout)['backend'], 'portable')
            self.assertEqual(json.loads(checked.stdout)['miner_use_fee']['fee_percent'], 3)
            self.assertFalse(marker.exists())
            tested = subprocess.run(command+['--self-test'], env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stderr)
            self.assertEqual(marker.read_text(), 'offline stub only')
            marker.unlink()
            refused = subprocess.run(command+['--self-test', '--backend', 'avx512_gfni'], env=environment,
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(refused.returncode, 78)
            self.assertFalse(marker.exists())


class TemperatureAndOwnership(unittest.TestCase):
    def test_temperature_maximum_and_sensor_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder)/'temp1_input', Path(folder)/'temp2_input']
            paths[0].write_text('60000'); paths[1].write_text('65000')
            monitor = launcher.TemperatureMonitor(paths, 75)
            self.assertEqual(monitor.read(), 65)
            paths[1].write_text('75000')
            with self.assertRaises(launcher.SafetyError): monitor.read()
            paths[1].unlink()
            with self.assertRaises(launcher.SafetyError): monitor.read()
        with self.assertRaises(launcher.SafetyError): launcher.TemperatureMonitor([], 75)
        with self.assertRaises(launcher.SafetyError): launcher.explicit_sensor('/etc/passwd')

    def test_inaccessible_other_proc_entry_does_not_abort_scan(self):
        with patch.object(Path, 'glob', return_value=[Path('/proc/1234/stat')]), \
             patch.object(Path, 'read_text', side_effect=PermissionError):
            self.assertEqual(launcher.owned_processes(5000), [])

    def test_cleanup_covers_second_process_group_and_preserves_sentinel(self):
        launcher.require_process_handles()
        sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        try:
            for mode in ('stop', 'thermal', 'sensor', 'phase'):
                with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                    path = Path(folder)/'owned.json'
                    script = ('import json,os,signal,time\n'
                              'signal.signal(signal.SIGINT,lambda *args:exit(0))\n'
                              'child=os.fork()\n'
                              'if child==0:\n'
                              ' os.setpgid(0,0)\n'
                              ' signal.signal(signal.SIGINT,signal.SIG_IGN)\n'
                              'else:\n'
                              ' open('+repr(str(path))+',"w").write(json.dumps([os.getpid(),child]))\n'
                              'while True:time.sleep(.05)\n')
                    requested = [None]
                    start = time.monotonic()
                    class Probe:
                        def read(self):
                            if time.monotonic()-start > .35:
                                if mode == 'stop': requested[0] = signal.SIGTERM
                                elif mode != 'phase': raise launcher.SafetyError('simulated '+mode+' failure')
                            return 60
                    if mode == 'stop': self.assertEqual(launcher.supervise([sys.executable, '-c', script], Probe(), requested), 0)
                    elif mode == 'phase':
                        self.assertIsNone(launcher.supervise([sys.executable, '-c', script], Probe(), requested,
                            switch_deadline=start+.4, shutdown_reserve=.05))
                    else:
                        with self.assertRaises(launcher.SafetyError): launcher.supervise([sys.executable, '-c', script], Probe(), requested)
                    self.assertIsNone(sentinel.poll())
                    self.assertTrue(path.exists())
                    for pid in json.loads(path.read_text()):
                        try: state = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[0]
                        except FileNotFoundError: continue
                        self.assertIn(state, ('Z', 'X'), 'Owned process survived cleanup')
        finally:
            sentinel.terminate(); sentinel.wait(timeout=5)

    def test_real_sigterm_reaches_supervisor_stop_path(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder)/'started'
            code = ('import pathlib,signal,sys\n'
                    'sys.path.insert(0,'+repr(str(ENTRY.parent))+')\n'
                    'import noid_launcher as m\n'
                    'requested=[None]\n'
                    'signal.signal(signal.SIGTERM,lambda n,f:requested.__setitem__(0,n))\n'
                    'child_code='+repr('import pathlib,time;pathlib.Path('+repr(str(marker))+').touch();time.sleep(30)')+'\n'
                    'raise SystemExit(m.supervise([sys.executable,"-c",child_code],None,requested))\n')
            child = subprocess.Popen([sys.executable, '-B', '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic()+5
                while not marker.exists() and child.poll() is None and time.monotonic() < deadline: time.sleep(.02)
                self.assertTrue(marker.exists())
                child.send_signal(signal.SIGTERM)
                self.assertEqual(child.wait(timeout=8), 0)
            finally:
                if child.poll() is None: child.kill(); child.wait(timeout=5)

    def test_stop_during_planned_cleanup_prevents_next_phase(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); marker = folder/'cleanup-started'; stub = folder/'client.py'
            stub.write_text('import signal,time\nsignal.signal(signal.SIGINT,signal.SIG_IGN)\ntime.sleep(30)\n')
            code = ('import json,pathlib,signal,sys,time,os\n'
                    'sys.path.insert(0,'+repr(str(ENTRY.parent))+')\n'
                    'import noid_launcher as m\n'
                    'requested=[None]\n'
                    'signal.signal(signal.SIGTERM,lambda n,f:requested.__setitem__(0,n))\n'
                    'original=m.cleanup\n'
                    'def cleanup(child,monitor=None,graceful=True,grace_deadline=None,requested=None):\n'
                    ' pathlib.Path('+repr(str(marker))+').touch()\n'
                    ' return original(child,monitor,graceful,time.monotonic()+.5,requested)\n'
                    'm.cleanup=cleanup\n'
                    'policy=json.loads(pathlib.Path('+repr(str(ENTRY.parent/'fee-policy.json'))+').read_text())\n'
                    'policy.update(cycle_seconds=2,user_seconds=.2,fee_seconds=1.8,fee_shutdown_reserve_seconds=.05)\n'
                    'args=m.parser().parse_args(["--pool","localhost:1","--wallet","o1"+"q"*20])\n'
                    'checks=[0]\n'
                    'def verify():\n'
                    ' checks[0]+=1\n'
                    ' if checks[0]>1:raise SystemExit(99)\n'
                    'raise SystemExit(m.scheduled_mining(args,pathlib.Path('+repr(str(stub))+'),pathlib.Path("/unused-engine"),'
                    'policy,None,requested,{"cpu_ids":sorted(os.sched_getaffinity(0))},verify))\n')
            process = subprocess.Popen([sys.executable, '-B', '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic()+5
                while not marker.exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.01)
                self.assertTrue(marker.exists())
                time.sleep(.03)
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=5), 0, 'A stop received during cleanup must prevent the next phase')
            finally:
                if process.poll() is None: process.kill(); process.wait(timeout=5)

    def test_delayed_phase_start_does_not_spawn_expired_worker(self):
        now = [100.0]
        def verify(): now[0] = 181.0
        with patch.object(launcher.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(launcher.subprocess, 'Popen') as spawn:
            self.assertIsNone(launcher.supervise(['/unused-worker'], None, [None], switch_deadline=180,
                                                 shutdown_reserve=4, prelaunch_check=verify))
            spawn.assert_not_called()

    def test_prelaunch_integrity_failure_never_spawns(self):
        def verify(): raise launcher.SafetyError('changed worker or fee policy')
        with patch.object(launcher.subprocess, 'Popen') as spawn:
            with self.assertRaises(launcher.SafetyError):
                launcher.supervise(['/unused-worker'], None, [None], prelaunch_check=verify)
            spawn.assert_not_called()


class TLSContext(unittest.TestCase):
    def test_default_tls_requires_certificate_and_hostname(self):
        context = client.tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


class FeePolicyAndSchedule(unittest.TestCase):
    def policy(self):
        return launcher.validate_fee_policy(json.loads((ENTRY.parent/'fee-policy.json').read_text()))

    def test_exact_disclosed_recipient_and_allocation(self):
        policy = self.policy()
        self.assertEqual(policy['fee_wallet'], 'o1206vtktn5pqhtnc9h689rgur2lc04jv20k6e7093xfpmc4jdr8rqfw9fs0')
        self.assertEqual(policy['fee_pool'], {'host': 'us3.innovlab.cc', 'port': 19601, 'tls': True, 'trust': 'default_certificate_store'})
        self.assertEqual(policy['fee_worker'], 'HyperNOID_DevFee')
        self.assertEqual((policy['user_seconds'], policy['fee_seconds'], policy['cycle_seconds']), (5820, 180, 6000))
        self.assertEqual(policy['fee_seconds']/policy['cycle_seconds'], .03)
        with self.assertRaises(launcher.SafetyError): launcher.validate_fee_policy({**policy, 'fee_percent': 5})

    def test_absolute_boundaries_jumps_skips_and_restart(self):
        schedule = launcher.FeeSchedule(self.policy(), 100)
        self.assertEqual(schedule.phase(100), ('user', 5920, 0))
        self.assertEqual(schedule.phase(5919.99), ('user', 5920, 0))
        self.assertEqual(schedule.phase(5920), ('fee', 6100, 0))
        schedule.skip_fee(0)
        self.assertEqual(schedule.phase(5930), ('user', 11920, 0))
        self.assertEqual(schedule.phase(11920), ('fee', 12100, 1))
        self.assertEqual(schedule.phase(18100), ('user', 23920, 3))  # Missed slots are not replayed.
        with self.assertRaises(launcher.SafetyError): schedule.phase(18099)
        restarted = launcher.FeeSchedule(self.policy(), 18300)
        self.assertEqual(restarted.phase(18300), ('user', 24120, 0))

    def test_custom_ca_is_user_only_and_fee_is_once(self):
        with tempfile.TemporaryDirectory() as folder:
            ca = Path(folder)/'user-ca.pem'; ca.write_text('fixture only')
            args = launcher.parser().parse_args(['--pool', 'user.example:19601', '--wallet', 'o1'+'q'*20,
                '--worker', 'UserWorker', '--threads', '7', '--ca-file', str(ca)])
            user = launcher.phase_command(args, Path('/client'), Path('/engine'), self.policy(), 'user')
            fee = launcher.phase_command(args, Path('/client'), Path('/engine'), self.policy(), 'fee')
            self.assertIn('--ca-file', user); self.assertNotIn('--ca-file', fee)
            self.assertNotIn('--once', user); self.assertIn('--once', fee)
            self.assertEqual(user[user.index('--payout-address')+1], args.wallet)
            self.assertEqual(fee[fee.index('--payout-address')+1], self.policy()['fee_wallet'])
            self.assertEqual(fee[fee.index('--host')+1], 'us3.innovlab.cc')
            self.assertEqual(user[user.index('--accounting')+1], 'user')
            self.assertEqual(fee[fee.index('--accounting')+1], 'fee')
            self.assertEqual(user[-3:], fee[-3:])  # Same engine and thread count.

    def test_failed_fee_resumes_user_without_catchup_or_losing_once(self):
        args = launcher.parser().parse_args(['--pool', 'user.example:19601', '--wallet', 'o1'+'q'*20, '--once'])
        requested = [None]; clock = [0.0]; phases = []; monitor = object(); active = [0]; checks = []
        def supervised(command, current_monitor, current_requested, **options):
            options['prelaunch_check']()
            self.assertEqual(active[0], 0); active[0] += 1
            self.assertIs(current_monitor, monitor); self.assertIs(current_requested, requested)
            phase = command[command.index('--accounting')+1]; phases.append(phase)
            self.assertIn('--once', command)
            try:
                if len(phases) == 1:
                    self.assertEqual(options['switch_deadline'], 5820)
                    clock[0] = 5820; return None  # Planned switch despite user --once.
                if len(phases) == 2:
                    self.assertEqual(options['switch_deadline'], 6000)
                    self.assertEqual(options['shutdown_reserve'], 4)
                    clock[0] = 5830; return 1  # Fee TLS/auth/connect/worker failure.
                self.assertEqual(options['switch_deadline'], 11820)
                return 1  # This user's --once disconnection ends the miner.
            finally: active[0] -= 1
        with patch.object(launcher, 'supervise', side_effect=supervised), \
             patch.object(launcher, 'probed_affinity', return_value=(0,)), contextlib.redirect_stdout(io.StringIO()):
            result = launcher.scheduled_mining(args, Path('/client'), Path('/engine'), self.policy(), monitor,
                                              requested, {}, lambda: checks.append('verified'), clock=lambda: clock[0])
        self.assertEqual(result, 1); self.assertEqual(phases, ['user', 'fee', 'user'])
        self.assertEqual(checks, ['verified']*3)

    def test_temperature_failure_during_fee_stops_all_work(self):
        args = launcher.parser().parse_args(['--pool', 'user.example:19601', '--wallet', 'o1'+'q'*20])
        clock = [0.0]; phases = []
        def supervised(command, *values, **options):
            phases.append(command[command.index('--accounting')+1])
            if len(phases) == 1: clock[0] = 5820; return None
            raise launcher.SafetyError('simulated thermal cutoff')
        with patch.object(launcher, 'supervise', side_effect=supervised), \
             patch.object(launcher, 'probed_affinity', return_value=(0,)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(launcher.SafetyError):
                launcher.scheduled_mining(args, Path('/client'), Path('/engine'), self.policy(), object(),
                                          [None], {}, lambda: None, clock=lambda: clock[0])
        self.assertEqual(phases, ['user', 'fee'])

    def test_integrity_change_between_phases_stops_instead_of_skipping_fee(self):
        args = launcher.parser().parse_args(['--pool', 'user.example:19601', '--wallet', 'o1'+'q'*20])
        clock = [0.0]; checks = [0]; spawned = []
        def verify():
            checks[0] += 1
            if checks[0] == 2: raise launcher.SafetyError('fee policy checksum changed')
        def supervised(command, *values, **options):
            options['prelaunch_check']()
            spawned.append(command[command.index('--accounting')+1])
            clock[0] = 5820; return None
        with patch.object(launcher, 'supervise', side_effect=supervised), \
             patch.object(launcher, 'probed_affinity', return_value=(0,)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(launcher.SafetyError):
                launcher.scheduled_mining(args, Path('/client'), Path('/engine'), self.policy(), None,
                                          [None], {}, verify, clock=lambda: clock[0])
        self.assertEqual(spawned, ['user'])


if __name__ == '__main__':
    unittest.main()
