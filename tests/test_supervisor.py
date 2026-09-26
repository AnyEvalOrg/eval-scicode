"""Image target ownership prerequisites without changing host credentials."""
import ast
import io
import stat
from types import SimpleNamespace
import pytest
from scicode.sandbox_runner import SETUP

HDF5 = b'\x89HDF\r\n\x1a\n'
MARKER = '/opt/scicode/test_data.sha256'
DIGEST = '8fb6e575b7b6dda5e48b04dea338fc6af4fe185774b8f19221c96945df9b4142'


def target_check(mode=0o400, uid=0, header=HDF5, marker=None, marker_mode=0o444, marker_uid=0):
    node=next(n for n in ast.parse(SETUP).body if isinstance(n,ast.FunctionDef) and n.name=='check_test_data')

    def fake_stat(path, **kwargs):
        if path == MARKER:
            if marker is None:
                raise FileNotFoundError(path)
            return SimpleNamespace(st_uid=marker_uid, st_mode=stat.S_IFREG|marker_mode)
        return SimpleNamespace(st_uid=uid, st_mode=stat.S_IFREG|mode)

    namespace={'os':SimpleNamespace(stat=fake_stat),
               'open':lambda path,*a:io.BytesIO(marker if path == MARKER else header)}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<target-check>','exec'),namespace)
    return namespace['check_test_data']


@pytest.mark.parametrize('mode,uid,header,valid',[
    (0o400,0,HDF5,True),(0o444,0,HDF5,False),
    (0o400,65532,HDF5,False),(0o400,0,b'bad data',False)])
def test_target_prerequisite(mode,uid,header,valid):
    check = target_check(mode, uid, header)
    if valid:check()
    else:
        with pytest.raises(RuntimeError):check()


@pytest.mark.parametrize('marker,expected,marker_mode,marker_uid,valid', [
    (None, None, 0o444, 0, True),                                  # scicode/scicode on its original image
    ((DIGEST + '\n').encode(), DIGEST, 0o444, 0, True),            # scicode_verified on the verified image
    ((DIGEST + '\n').encode(), None, 0o444, 0, False),             # scicode/scicode on the verified image
    (None, DIGEST, 0o444, 0, False),                               # scicode_verified on the original image
    ((DIGEST + '\n').encode(), '0' * 64, 0o444, 0, False),         # another target set
    ((DIGEST + '\n').encode(), DIGEST, 0o666, 0, False),           # writable marker
    ((DIGEST + '\n').encode(), DIGEST, 0o444, 65532, False),       # candidate-owned marker
    ((DIGEST + '\nextra').encode(), DIGEST, 0o444, 0, False),
    ((DIGEST + '\n').encode(), [DIGEST], 0o444, 0, False),         # non-string request value
])
def test_target_marker_binds_task_to_image(marker, expected, marker_mode, marker_uid, valid):
    check = target_check(marker=marker, marker_mode=marker_mode, marker_uid=marker_uid)
    if valid:
        check(expected)
    else:
        with pytest.raises(RuntimeError):
            check(expected)


def test_setup_passes_the_requested_digest():
    assert 'check_test_data(request.get("targets_sha256"))' in SETUP
