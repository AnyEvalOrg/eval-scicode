"""Real two-process RPC and signing code; Linux containment is operator-tested.

Host fixture substitutes only UID/proc/security prerequisites and HDF5 targets.
No candidate or test computations are moved into the test runner.
"""
import ast
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

from scicode.execution import execution_request
from scicode.receipts import verify_receipt
from scicode.sandbox_runner import RUNNER
from scicode.safe_serialization import dumps


def run_signed(tmp_path, code, tests, targets, dependencies='import numpy as np', timeout=10,
               output_limit=32*1024*1024, fault=None):
    work = tmp_path / 'scicode-fixture'
    work.mkdir()
    package = str(Path('scicode').resolve())
    paths = [package, str(Path('.build/test-deps').resolve()), *sys.path]
    request = execution_request(code, {'step_number': 'fixture.1', 'test_cases': tests}, timeout, dependencies)
    request['output_limit'] = output_limit
    # Targets belong to the trusted fixture, not argv or one aggregate RPC
    # frame. Large reference sets can exceed both ARG_MAX and MAX_BYTES.
    target_files = []
    for index, target in enumerate(targets):
        path = tmp_path / f'target-{index}.json'
        path.write_bytes(dumps(target))
        target_files.append(str(path))
    stdout = io.StringIO()
    launches = []
    processes = {}

    def popen(argv, **kwargs):
        launches.append((argv, kwargs.copy()))
        worker = Path(argv[argv.index('-I') + (2 if '-S' in argv else 1)]).stem
        actual_args = [str(Path(package) / (worker + '.py')), *argv[-3:]]
        if worker == 'candidate_worker':
            private = json.loads(Path(argv[-3]).read_text())
            assert set(private) == {'code', 'timeout'}
            assert kwargs['pass_fds'] == tuple(map(int, argv[-2:]))
            assert kwargs['preexec_fn'] is restrict
        else:
            private = json.loads(Path(argv[-3]).read_text())
            assert 'code' not in private
            assert private['tests'] == tests
            assert '-S' in argv and kwargs['cwd'] == '/'
            assert kwargs['pass_fds'] == tuple(map(int, argv[-2:]))
            assert not set(kwargs['pass_fds']) & set(launches[0][1]['pass_fds'])
        wrapper = ('import sys,resource;sys.path[:0]=' + repr(paths) + ';sys.argv=' + repr(actual_args) + ';'
                   'import ' + worker + ' as worker;')
        if worker == 'comparison_worker':
            wrapper += ('import types;from pathlib import Path;from safe_serialization import loads;'
                        'worker.prerequisites=lambda:resource.setrlimit(resource.RLIMIT_CPU,' + repr((2, 2) if fault and 'while True' in fault[2] else (300, 300)) + ');'
                        'worker.trusted_paths=lambda:None;worker.deny_network=lambda:None;'
                        'sys.modules["process_data"]=types.SimpleNamespace(process_hdf5_to_tuple=lambda *a:[loads(Path(p).read_bytes()) for p in ' + repr(target_files) + ']);')
            if fault:
                module, name, body = fault
                wrapper += 'import ' + module + ';exec(' + repr('def fail(*args, **kwargs):\n    ' + body + '\n' + module + '.' + name + '=fail') + ');'
        wrapper += 'worker.main()'
        process = subprocess.Popen([sys.executable, '-I', '-S', '-c', wrapper], **kwargs)
        processes[process.pid] = process
        return process

    def restrict():
        pass

    def kill_group(pid):
        # Host fixtures never fork. Use the owned Popen handle on macOS, whose
        # sandbox can deny killpg after the leader exits. Linux group/UID
        # cleanup is retained in production and tested with authored proc data.
        process = processes[pid]
        if process.poll() is None:
            try:
                process.kill()
            except PermissionError:
                # A just-exited fixture may be waiting to be reaped.
                process.wait(timeout=1)

    class Done(Exception):
        pass

    def exit_(value):
        raise Done()

    os_proxy = SimpleNamespace(**{n: getattr(os, n) for n in dir(os)})
    os_proxy.getuid = lambda: 0
    os_proxy.chown = lambda *a: None
    os_proxy._exit = exit_
    namespace = dict(os=os_proxy, sys=SimpleNamespace(executable=sys.executable, stdout=stdout),
                     work=str(work), request=request, limit=output_limit, key=b'a'*32,
                     CANDIDATE_UID=os.getuid(), CANDIDATE_GID=os.getgid(),
                     libc=SimpleNamespace(prctl=lambda *a: 0), resource=resource,
                     tempfile=tempfile, time=time, threading=threading, json=json,
                     hashlib=hashlib, hmac=hmac, restrict_child=restrict,
                     kill_group=kill_group, sweep_uid=lambda: None,
                     watch_memory=lambda *a: None, watch_disk=lambda *a: None,
                     subprocess=SimpleNamespace(Popen=popen, DEVNULL=subprocess.DEVNULL,
                                                TimeoutExpired=subprocess.TimeoutExpired))
    tree = ast.parse(RUNNER)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_proxy_tests']
    # Include status/verdict initialization and the actual final signing block.
    nodes += tree.body[-3:]
    try:
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<real-supervisor>', 'exec'), namespace)
    except Done:
        pass
    # Production receipt path grammar is /tmp/scicode-*. Translate only that
    # field from pytest's temp path and reauthenticate it for the validator.
    envelope = json.loads(stdout.getvalue())
    assert hmac.compare_digest(envelope['tag'], hmac.new(b'a'*32, envelope['body'].encode(), hashlib.sha256).hexdigest())
    body = json.loads(envelope['body'])
    body['cwd'] = '/tmp/scicode-fixture'
    body_text = json.dumps(body)
    wire = json.dumps({'body': body_text, 'tag': hmac.new(b'a'*32, body_text.encode(), hashlib.sha256).hexdigest()})
    receipt = verify_receipt(wire, b'a'*32)
    assert receipt is not None
    assert 'PRIVATE' not in wire and 'candidate()' not in wire
    assert len(launches) == 2
    return receipt
