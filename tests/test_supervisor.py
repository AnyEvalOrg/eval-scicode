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
import pytest
from scicode.sandbox_runner import RUNNER, SETUP
from scicode.receipts import verify_receipt, receipt_failure, FLAGS


@pytest.mark.parametrize('case',['pass','mismatch','forge','symlink','hardlink','fifo','invalid','failure','supervisor_error'])
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
        return status,b'PRIVATE_STDOUT'
    namespace={'sys':SimpleNamespace(path=list(sys.path),stdout=stdout),'os':os_proxy,'stat':stat,
               'json':json,'hashlib':hashlib,'hmac':hmac,'work':str(work),'key':key,'limit':32*1024*1024,
               'CANDIDATE_UID':os.getuid(),'CANDIDATE_GID':os.getgid(),'request':request,
               'run_step':run_step,'status':status,'verdicts':[False]}
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
