"""Test the contribution's generated RPM against isolated synthetic runtime providers."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

def run(args, **options):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options).stdout

source = Path(os.environ['SOURCE_ROOT'])
expected_after = os.environ['RPM_EXPECT'] == 'after'
proof = {'revision': os.environ['REVISION'], 'expected': os.environ['RPM_EXPECT'], 'architectures': {}}
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    stage = root / 'stage'
    (stage / 'usr/bin').mkdir(parents=True)
    (stage / 'usr/share').mkdir(parents=True)
    for name in ['vectorcraft', 'vectorcraft-cli']:
        path = stage / 'usr/bin' / name
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    config = yaml.safe_load((source / 'packaging/linux/nfpm.yaml').read_text().replace('${STAGE}', str(stage)))
    config['scripts'] = {}  # Metadata/dependency checks only; no package scripts execute.
    cfg = root / 'nfpm.yaml'
    cfg.write_text(yaml.safe_dump(config, sort_keys=False))
    core = ['libc.so.6()(64bit)', 'libc.so.6(GLIBC_2.35)(64bit)', 'libm.so.6()(64bit)', 'libgcc_s.so.1()(64bit)', 'libxkbcommon.so.0()(64bit)']
    cases = {
        'egl': (core + ['libEGL.so.1()(64bit)'], True),
        'vulkan': (core + ['libvulkan.so.1()(64bit)'], True),
        'no-backend': (core, False),
        'no-xkb': ([x for x in core if not x.startswith('libxkbcommon')] + ['libEGL.so.1()(64bit)'], False),
        'old-glibc': ([x for x in core if 'GLIBC_2.35' not in x] + ['libc.so.6(GLIBC_2.34)(64bit)', 'libEGL.so.1()(64bit)'], False),
        'no-gcc': ([x for x in core if not x.startswith('libgcc_s')] + ['libEGL.so.1()(64bit)'], False),
    }
    for arch in ['amd64', 'arm64']:
        env = {**os.environ, 'VERSION': '0.9.0', 'NFPM_ARCH': arch, 'VECTORCRAFT_MAINTAINER': 'Fixture <fixture@example.invalid>'}
        package = root / (arch + '.rpm')
        run(['nfpm', 'package', '-f', str(cfg), '-p', 'rpm', '-t', str(package)], env=env, cwd=source)
        requirements = run(['rpm', '-qp', '--requires', str(package)]).splitlines()
        results = {}
        for name, (caps, wanted) in cases.items():
            provider = {'name': 'fixture-' + name, 'arch': 'all', 'platform': 'linux', 'version': '1.0.0', 'provides': caps, 'description': 'Synthetic dependency provider for an isolated RPM database', 'maintainer': 'Fixture <fixture@example.invalid>'}
            provider_cfg = root / ('provider-' + name + '.yaml')
            provider_cfg.write_text(yaml.safe_dump(provider))
            provider_rpm = root / ('provider-' + name + '.rpm')
            run(['nfpm', 'package', '-f', str(provider_cfg), '-p', 'rpm', '-t', str(provider_rpm)])
            database = root / (arch + '-' + name)
            database.mkdir()
            command = ['rpm', '--root', str(database), '--dbpath', '/rpmdb']
            run(command + ['--initdb'])
            run(command + ['-i', '--noscripts', str(provider_rpm)])
            result = subprocess.run(command + ['--test', '--noscripts', '--ignorearch', '-U', str(package)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            satisfied = result.returncode == 0
            expected = wanted if expected_after else False
            assert satisfied == expected, (arch, name, expected, result.returncode, result.stderr)
            results[name] = {'satisfied': satisfied, 'expected': expected}
        proof['architectures'][arch] = {'requirements': requirements, 'cases': results}
print(json.dumps(proof, indent=2))
print('RPM metadata summary: all 12 isolated provider checks passed')
