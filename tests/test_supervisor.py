"""Execute the real signing/comparison body with authored children, no host UID changes.

Linux containment is separately exercised by the operator's canonical command.
"""
import ast
import base64
import ctypes
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from scicode.sandbox_runner import RUNNER, SETUP
from scicode.receipts import verify_receipt, receipt_failure, FLAGS


@pytest.mark.parametrize('case',['pass','mismatch','forge','symlink','hardlink','fifo','invalid','failure','supervisor_error',
    'exponent','result_overflow','worker_cpu','worker_wall','worker_memory','worker_crash','worker_compare_wall','worker_compare_memory','worker_prerequisite','late_prerequisite'])
def test_actual_supervisor_signs_only_private_comparisons(tmp_path,monkeypatch,case):
    work=tmp_path/'scicode-fixture';work.mkdir()
    key=b'a'*32
    package=Path('scicode').resolve()
    monkeypatch.syspath_prepend(str(package))
    target_reads=[]
    def targets(step,count):
        target_reads.append((step,count));return [7.0]
    monkeypatch.setitem(sys.modules,'process_data',SimpleNamespace(process_hdf5_to_tuple=targets))
    code='def candidate(): return '+('7.' if case=='pass' else '9.')
    request={'tests':['assert np.allclose(candidate(), target)'],'code':'import numpy as np\n'+code,'step_id':'fixture.1','timeout':2}
    stdout=io.StringIO()
    status={f:False for f in FLAGS};status['returncode']=0
    class Done(Exception):pass
    def exit_(value):raise Done()
    os_proxy=SimpleNamespace(**{n:getattr(os,n) for n in dir(os)})
    os_proxy.getuid=lambda:0
    os_proxy.chown=lambda *a:None
    os_proxy._exit=exit_
    def run_step(argv,timeout,cwd):
        assert not target_reads, 'Root opened targets before candidate launch'
        source=Path(argv[-1]).read_text()
        # Production driver contains no target references or private assertions.
        assert 'target' not in source
        assert 'assert np.allclose' not in source
        if case=='supervisor_error':raise OSError('PRIVATE_ERROR')
        if case=='failure':return {**status,'returncode':2},b'PRIVATE_STDOUT'
        wrapper='import sys,runpy;sys.path[:0]='+repr([str(package),str(Path('.build/test-deps').resolve())])+';runpy.run_path('+repr(argv[-1])+')'
        child=subprocess.run([sys.executable,'-I','-c',wrapper],cwd=cwd,capture_output=True,timeout=10)
        assert child.returncode==0,child.stderr.decode()
        output=Path(cwd)/'result-0'
        if case=='forge':output.write_text(json.dumps({'verdicts':[True]}))
        elif case in {'symlink','fifo','hardlink'}:
            output.unlink()
            if case=='symlink':output.symlink_to('/etc/passwd')
            elif case=='fifo':os.mkfifo(output)
            else:
                other=Path(cwd)/'other';other.write_bytes(b'private');os.link(other,output)
        elif case=='invalid':output.write_bytes(b'\x80pickle is forbidden')
        elif case=='result_overflow':output.write_bytes(b'x' * 1024)
        elif case=='exponent':
            output.write_text(json.dumps(['list', [['sympy_float', '1e' + '9' * 8000, 53]]]))
        elif case.startswith('worker_'):
            from scicode.safe_serialization import dumps
            output.write_bytes(dumps([7.]))

        return status,b'PRIVATE_STDOUT'
    # Execute the production worker in a separate interpreter. Only Linux
    # prerequisites / AS are stubbed on this macOS host; CPU and wall deadlines
    # remain real. Synthetic targets replace the unavailable HDF5 asset.
    fault = {
        'worker_cpu': 'while True: pass',
        'worker_wall': 'import time; time.sleep(60)',
        'worker_memory': 'raise MemoryError()',
        'worker_crash': 'import os; os._exit(139)',
        'worker_compare_wall': 'import time; time.sleep(60)',
        'worker_compare_memory': 'raise MemoryError()',
    }.get(case)
    launches = []
    def worker_popen(argv, **kwargs):
        launches.append(argv)
        wrapper = (
            'import sys,resource,types;sys.path[:0]=' + repr([str(package), str(Path('.build/test-deps').resolve())]) + ';'
            'import comparison_worker as worker;'
            # macOS cannot lower RLIMIT_AS. Validate the exact request, then
            # retain the native CPU limit (shortened to keep regressions fast).
            'original_limit=resource.setrlimit;'
            'resource.setrlimit=lambda kind,bounds: '
            '(None if kind==resource.RLIMIT_AS else original_limit(kind, (1,1) if kind==resource.RLIMIT_CPU else bounds));'
            'worker.os.getuid=lambda:0;'
            'worker.ctypes.CDLL=lambda *args:types.SimpleNamespace(prctl=lambda *args:0);'
            'sys.modules["process_data"]=types.SimpleNamespace(process_hdf5_to_tuple=lambda step,count:[7.]*count);'
            'sys.argv=' + repr(['comparison_worker.py', *argv[-2:]]) + ';'
        )
        if fault:
            module, function = ('test_plan', 'compare_plan') if case.startswith('worker_compare_') else ('safe_serialization', 'loads')
            wrapper += 'import ' + module + ';exec(' + repr('def fail(*args):\n    ' + fault + '\n' + module + '.' + function + '=fail') + ');'
        if case == 'worker_prerequisite':
            wrapper += 'worker.prerequisites=lambda:(_ for _ in ()).throw(RuntimeError());'
        wrapper += 'worker.main()'
        return subprocess.Popen([sys.executable, '-I', '-c', wrapper], **kwargs)

    root_decodes = []
    import safe_serialization
    def forbidden_root_decode(data):
        root_decodes.append(len(data))
        # Keep a reverted supervisor from hanging on the actual giant exponent.
        raise ValueError('Root must not decode candidate bytes')
    if case == 'exponent':
        monkeypatch.setattr(safe_serialization, 'loads', forbidden_root_decode)
    namespace={'sys':SimpleNamespace(path=list(sys.path),stdout=stdout),'os':os_proxy,'stat':stat,
               'json':json,'hashlib':hashlib,'hmac':hmac,'work':str(work),'key':key,'limit':1024 if case=='result_overflow' else 32*1024*1024,
               'CANDIDATE_UID':os.getuid(),'CANDIDATE_GID':os.getgid(),'request':request,
               'run_step':run_step,'status':status,'verdicts':[False],
               'libc':SimpleNamespace(prctl=lambda *a: -1 if case=='late_prerequisite' else 0),
               'resource':Mock(), 'tempfile':__import__('tempfile'),
               'subprocess':SimpleNamespace(Popen=worker_popen, DEVNULL=subprocess.DEVNULL,
                                            TimeoutExpired=subprocess.TimeoutExpired)}
    namespace['sys'].executable = sys.executable
    functions = [n for n in ast.parse(RUNNER).body if isinstance(n, ast.FunctionDef) and n.name=='run_comparisons']
    # Exercise the actual supervisor deadline with a shorter test budget.
    for function in functions:
        for node in ast.walk(function):
            if isinstance(node, ast.keyword) and node.arg=='timeout' and isinstance(node.value, ast.Constant) and node.value.value==10:
                node.value.value = 2
    exec(compile(ast.Module(body=functions,type_ignores=[]),'<comparison-supervisor>','exec'),namespace)

    final=ast.parse(RUNNER).body[-1]
    with pytest.raises(Done):exec(compile(ast.Module(body=[final],type_ignores=[]),'<supervisor>','exec'),namespace)
    wire=stdout.getvalue()
    # Receipt cwd must be production-shaped; replace and re-sign for validation.
    envelope=json.loads(wire);body=json.loads(envelope['body']);body['cwd']='/tmp/scicode-fixture'
    encoded=json.dumps(body);wire=json.dumps({'body':encoded,'tag':hmac.new(key,encoded.encode(),hashlib.sha256).hexdigest()})
    receipt=verify_receipt(wire,key)
    assert receipt is not None
    assert (receipt_failure(receipt) is None)==(case=='pass')
    assert receipt['verdicts']==[case=='pass']
    assert 'PRIVATE' not in wire and 'candidate()' not in wire
    assert not root_decodes, 'Signing supervisor decoded candidate-controlled bytes'
    assert receipt['overflow'] == (case=='result_overflow')
    if case=='late_prerequisite':
        assert receipt['supervisor_error']
    if case.startswith('worker_') or case == 'exponent':
        assert len(launches) == 1



@pytest.mark.parametrize('mode,uid,header,valid',[
    (0o400,0,b'\x89HDF\r\n\x1a\n',True),(0o444,0,b'\x89HDF\r\n\x1a\n',False),
    (0o400,65532,b'\x89HDF\r\n\x1a\n',False),(0o400,0,b'bad data',False)])
def test_target_prerequisite(mode,uid,header,valid):
    node=next(n for n in ast.parse(SETUP).body if isinstance(n,ast.FunctionDef) and n.name=='check_test_data')
    namespace={'os':SimpleNamespace(stat=lambda *a,**k:SimpleNamespace(st_uid=uid,st_mode=stat.S_IFREG|mode)),
               'open':lambda *a:io.BytesIO(header)}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<target-check>','exec'),namespace)
    if valid:namespace['check_test_data']()
    else:
        with pytest.raises(RuntimeError):namespace['check_test_data']()
