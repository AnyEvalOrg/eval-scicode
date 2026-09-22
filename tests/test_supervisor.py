"""Image target ownership prerequisites without changing host credentials."""
import ast
import io
import stat
from types import SimpleNamespace
import pytest
from scicode.sandbox_runner import SETUP


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
