#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Local build, CPU-gated offline validation and integrity metadata for HyperNOID.

This helper never connects to a pool or installs/starts a mining service.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT/'build'
VERSION = '1.0.0'
ORDER = ('avx512_gfni', 'avx2_vpclmul256', 'avx2_pclmul_packed', 'ssse3_pclmul128', 'portable')
RUNTIME = ('hypernoid-miner', 'launcher/hypernoid-miner', 'launcher/noid_launcher.py',
           'launcher/noid_stratum.py', 'launcher/fee-policy.json')
BASELINE_X86 = ['-march=x86-64', '-mtune=generic', '-mno-sse3', '-mno-ssse3', '-mno-sse4.1',
                '-mno-sse4.2', '-mno-avx', '-mno-avx2', '-mno-avx512f', '-mno-pclmul',
                '-mno-gfni', '-mno-vpclmulqdq', '-mno-bmi', '-mno-bmi2', '-mno-aes']


def architecture(value):
    value=value.lower()
    return {'amd64':'x86_64', 'arm64':'aarch64'}.get(value,value)


def run(argv, **kwargs):
    print('+ '+shlex.join(map(str,argv)),flush=True)
    return subprocess.run(list(map(str,argv)),check=True,**kwargs)


def compiler():
    return shlex.split(os.environ.get('CC','cc'))


def available(arch):
    names=ORDER if arch=='x86_64' else ('portable',)
    return [name for name in names if (ROOT/'engines'/name/'Makefile').is_file()]


def build_probe(arch):
    BUILD.mkdir(exist_ok=True)
    flags=['-O2','-Wall','-Wextra','-std=c11','-fno-tree-vectorize','-fno-tree-slp-vectorize']
    if arch=='x86_64': flags+=BASELINE_X86
    elif arch=='aarch64': flags+=['-march=armv8-a']
    # Deliberately do not accept CFLAGS: the pre-dispatch helper must stay baseline-safe.
    run([*compiler(),*flags,ROOT/'engines/cpu_probe.c','-o',BUILD/'cpu-probe'])


def build_backends(arch):
    names=available(arch)
    if not names: raise RuntimeError('No backend source is available for '+arch)
    env=dict(os.environ)
    env.pop('MAKEFLAGS',None)
    for name in names:
        run(['make','-B','-C',ROOT/'engines'/name,'all'],env=env)


def capabilities():
    path=BUILD/'cpu-probe'
    result=subprocess.run([str(path)],text=True,capture_output=True,check=True,timeout=30)
    value=json.loads(result.stdout)
    if value.get('schema')!=1 or not isinstance(value.get('supported_backends'),list):
        raise RuntimeError('Invalid capability-probe response')
    return value


def check(backend,arch,all_supported=False):
    info=capabilities()
    names=[name for name in ORDER if name in info['supported_backends']
           and (ROOT/'engines'/name/'noid-worker').is_file()]
    if all_supported:
        selected=names
    elif backend=='auto':
        selected=names[:1]
    elif backend not in ORDER:
        raise RuntimeError('Unknown backend '+backend)
    elif backend not in names:
        raise RuntimeError('Backend is unsupported by this CPU/OS or has not been built: '+backend)
    else:
        selected=[backend]
    if not selected: raise RuntimeError('No supported built backend is available')
    env=dict(os.environ)
    env.pop('MAKEFLAGS',None)
    for name in selected:
        makefile=(ROOT/'engines'/name/'Makefile').read_text()
        if not re.search(r'^check-offline:',makefile,re.M):
            raise RuntimeError('Backend lacks the required socket-free check-offline target: '+name)
        run(['make','-C',ROOT/'engines'/name,'check-offline'],env=env)


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def glibc_versions(path):
    result=subprocess.run(['readelf','--version-info',str(path)],text=True,capture_output=True,check=True)
    versions=sorted(set(re.findall(r'GLIBC_[0-9.]+',result.stdout)),
                    key=lambda value:tuple(map(int,value.removeprefix('GLIBC_').split('.'))))
    return {'symbol_versions':versions,'minimum_glibc':versions[-1].removeprefix('GLIBC_') if versions else None}


def manifest(arch):
    provenance=json.loads((ROOT/'engines/PROVENANCE.json').read_text())
    names=[name for name in available(arch) if (ROOT/'engines'/name/'noid-worker').is_file()]
    required=['build/cpu-probe',*RUNTIME,*['engines/'+name+'/noid-worker' for name in names]]
    files={}
    for name in required:
        path=ROOT/name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('Missing or redirected runtime file: '+name)
        files[name]=hash_file(path)
    backends={}
    binaries={}
    for name in names:
        binary=ROOT/'engines'/name/'noid-worker'
        version=subprocess.check_output([str(binary),'--version'],text=True,timeout=10).strip()
        if name in provenance['backends']:
            source=provenance['backends'][name]
            origin=source['provenance']['snapshot']+'; core '+source['core_version']
            required_features=source['required_features']
        else:
            source=json.loads((ROOT/'engines'/name/'PROVENANCE.json').read_text())
            origin=source.get('source_family','CPU HyperNOID Miner portable source')
            required_features=[]
        backends[name]={'engine_version':version,'provenance':origin,
                        'required_features':required_features,
                        'source_provenance':'engines/PROVENANCE.json' if name!='portable' else 'engines/portable/PROVENANCE.json'}
        binaries['engines/'+name+'/noid-worker']={'sha256':files['engines/'+name+'/noid-worker'],**glibc_versions(binary)}
    binaries['build/cpu-probe']={'sha256':files['build/cpu-probe'],**glibc_versions(BUILD/'cpu-probe')}
    runtime={'schema':1,'package_version':VERSION,'product':'CPU HyperNOID Miner',
             'architecture':arch,'files':dict(sorted(files.items())),'backends':backends}
    info={'schema':1,'package_version':VERSION,'architecture':arch,
          'compiler':subprocess.check_output([*compiler(),'--version'],text=True).splitlines()[0],
          'python':platform.python_version(),
          'make':subprocess.check_output(['make','--version'],text=True).splitlines()[0],
          'binutils':subprocess.check_output(['objdump','--version'],text=True).splitlines()[0],
          'binaries':binaries,
          'validation_note':'Build identity only. Separate completed test logs establish validation; no performance claim is implied.'}
    BUILD.mkdir(exist_ok=True)
    for filename,data in [('runtime-manifest.json',runtime),('build-info.json',info)]:
        temporary=BUILD/(filename+'.tmp')
        temporary.write_text(json.dumps(data,indent=2)+'\n')
        temporary.replace(BUILD/filename)
    print('Wrote build/runtime-manifest.json and build/build-info.json',flush=True)


def check_probe(arch):
    flags=['-O2','-Wall','-Wextra','-std=c11','-fno-tree-vectorize','-fno-tree-slp-vectorize']
    if arch=='x86_64': flags+=BASELINE_X86
    elif arch=='aarch64': flags+=['-march=armv8-a']
    output=BUILD/'test-cpu-probe'
    run([*compiler(),*flags,ROOT/'engines/test_cpu_probe.c','-o',output])
    run([output])
    info=capabilities()
    if info.get('cpu_ids')!=sorted(os.sched_getaffinity(0)):
        raise RuntimeError('Capability probe did not report the exact allowed CPU set')
    if arch=='x86_64':
        dump=subprocess.check_output(['objdump','-d','-w',str(BUILD/'cpu-probe')],text=True)
        forbidden=[]
        for line in dump.splitlines():
            match=re.match(r'^\s*[0-9a-f]+:\s+((?:[0-9a-f]{2}\s+)+)\s*([a-zA-Z0-9_.]+)',line)
            if not match: continue
            raw,name=match.groups()
            bytes_=[int(value,16) for value in raw.split()]
            if name.startswith('v') or 'pclmul' in name or 'gf2p8' in name or name in {
                'pdep','pext','mulx','shlx','shrx','sarx','bzhi','rorx','andn','bextr','blsi','blsmsk','blsr',
                'pshufb','popcnt','aesenc','aesdec'} or (bytes_ and bytes_[0] in {0xc4,0xc5,0x62}):
                forbidden.append(line.strip())
        if forbidden: raise RuntimeError('Non-baseline ISA in capability probe: '+repr(forbidden))
        print('PASS: capability probe has no VEX/EVEX, PCLMUL, GFNI, AVX, BMI/BMI2, or SSSE3 instructions',flush=True)
    print('PASS: live capability probe enumerates the exact process affinity and returns a supported fallback',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['probe','backends','check','check-supported','check-probe','manifest'])
    parser.add_argument('--arch',default=platform.machine())
    parser.add_argument('--backend',default='auto')
    args=parser.parse_args()
    arch=architecture(args.arch)
    if platform.system()!='Linux': raise RuntimeError('CPU HyperNOID Miner builds require Linux')
    if args.action=='probe': build_probe(arch)
    elif args.action=='backends': build_backends(arch)
    elif args.action=='manifest': manifest(arch)
    elif args.action=='check-probe': check_probe(arch)
    else: check(args.backend,arch,args.action=='check-supported')


if __name__=='__main__':
    try: main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        raise SystemExit('Build/check failed: '+str(exc))
