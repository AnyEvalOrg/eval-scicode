import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from functools import partial
import pytest
from scicode.sandbox_runner import SETUP, RUNNER, CLEANUP_COMMAND, QUIESCENCE_COMMAND
def test_supervisor_preserves_required_security_contract():
    tree = ast.parse(RUNNER)
    restrict = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'restrict_child'))
    calls = [ast.unparse(n.value) for n in restrict.body if isinstance(n, ast.Expr)]
    assert calls.index('os.setgroups([])') < calls.index('os.setresgid(CANDIDATE_GID, CANDIDATE_GID, CANDIDATE_GID)') < calls.index('os.setresuid(CANDIDATE_UID, CANDIDATE_UID, CANDIDATE_UID)') < calls.index('resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))')
    for text in ['libc.prctl(4, 0, 0, 0, 0)', 'libc.prctl(38, 1, 0, 0, 0)', 'libc.prctl(8, 0, 0, 0, 0)', 'libc.prctl(36, 1, 0, 0, 0)', 'os.killpg(pgid, sig)', 'os.O_NOFOLLOW', 'sweep_uid()', 'close_fds=True', 'start_new_session=True', 'preexec_fn=restrict_child', 'os.unlink(request_path)']:
        assert text in RUNNER
    assert 'shell=True' not in RUNNER and 'bash' not in RUNNER
    assert CLEANUP_COMMAND[-4:] == ['/usr/bin/pkill', '-KILL', '-u', '65532']

@pytest.mark.parametrize('nofile', [256, 1024])
def test_candidate_preexec_limits_and_oom_preference(nofile):
    from types import SimpleNamespace
    from unittest.mock import Mock, mock_open
    import resource
    tree = ast.parse(RUNNER)
    restrict = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'restrict_child'))
    limits = Mock()
    fake_resource = SimpleNamespace(**{name: getattr(resource, name) for name in ('RLIMIT_NPROC', 'RLIMIT_AS', 'RLIMIT_DATA', 'RLIMIT_FSIZE', 'RLIMIT_CORE', 'RLIMIT_NOFILE')}, setrlimit=limits)
    opened = mock_open()
    fake_os = Mock()
    namespace = dict(libc=SimpleNamespace(prctl=lambda *args: 0), os=fake_os, resource=fake_resource, CANDIDATE_UID=65532, CANDIDATE_GID=65532, limit=4096, open=opened)
    exec(compile(ast.Module(body=[restrict], type_ignores=[]), '<preexec>', 'exec'), namespace)
    namespace['restrict_child'](nofile)
    assert limits.call_args_list == [((resource.RLIMIT_NPROC, (64, 64)),), ((resource.RLIMIT_NOFILE, (nofile, nofile)),), ((resource.RLIMIT_AS, (5 * 1024 ** 3, 5 * 1024 ** 3)),), ((resource.RLIMIT_DATA, (5 * 1024 ** 3, 5 * 1024 ** 3)),), ((resource.RLIMIT_FSIZE, (4096, 4096)),), ((resource.RLIMIT_CORE, (0, 0)),)]
    opened.assert_called_once_with('/proc/self/oom_score_adj', 'w')
    opened().write.assert_called_once_with('1000')
    fake_os.setresuid.assert_called_once_with(65532, 65532, 65532)

@pytest.mark.parametrize('entry_error', [PermissionError, FileNotFoundError, ProcessLookupError])
@pytest.mark.parametrize('rss_kib, exceeded', [(4096 * 1024, False), (4096 * 1024 + 1, True)])
def test_watchdog_sums_only_candidate_rss_and_kills_detached_sessions(rss_kib, exceeded, entry_error):
    from io import StringIO
    from types import SimpleNamespace
    from unittest.mock import Mock
    import signal
    tree = ast.parse(RUNNER)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {'candidate_rss', 'kill_candidate', 'watch_memory'}]
    proc = {'11': f'Uid:\t65532 65532 65532 65532\nVmRSS:\t{rss_kib // 2} kB\n', '12': f'Uid:\t65532 65532 65532 65532\nVmRSS:\t{rss_kib - rss_kib // 2} kB\n', '13': 'Uid:\t0 0 0 0\nVmRSS:\t9999999 kB\n', '14': 'Uid:\t65532 65532 65532 65532\nState:\tZ (zombie)\n'}

    def opened(path):
        pid = path.split('/')[2]
        if pid == '15':
            raise entry_error(path)
        return StringIO(proc[pid])
    fake_os = SimpleNamespace(listdir=lambda _: [*proc, '15', 'self'], kill=Mock(), killpg=Mock())
    stopped = Mock()
    stopped.is_set.side_effect = [False, True]
    namespace = dict(os=fake_os, open=opened, signal=signal, CANDIDATE_UID=65532)
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<watchdog>', 'exec'), namespace)
    assert namespace['candidate_rss']() == rss_kib * 1024
    status = dict(memory_exceeded=False, peak_candidate_rss_bytes=0)
    namespace['watch_memory'](11, stopped, status)
    assert status == dict(memory_exceeded=exceeded, peak_candidate_rss_bytes=rss_kib * 1024)
    if exceeded:
        fake_os.killpg.assert_called_once_with(11, signal.SIGKILL)
        assert [call.args for call in fake_os.kill.call_args_list] == [(11, signal.SIGKILL), (12, signal.SIGKILL), (14, signal.SIGKILL)]
        stopped.wait.assert_not_called()
    else:
        fake_os.kill.assert_not_called()
        stopped.wait.assert_called_once_with(0.05)

@pytest.mark.parametrize('exceeded', [False, True])
def test_disk_watchdog_counts_allocated_blocks_across_both_roots(tmp_path, exceeded):
    import errno
    import stat
    from unittest.mock import Mock
    tmp, shm, outside = [tmp_path / name for name in ('tmp', 'shm', 'outside')]
    for directory in (tmp / 'work', tmp / 'sibling', shm, outside):
        directory.mkdir(parents=True)
    files = [tmp / 'work' / 'file', tmp / 'sibling' / 'file', shm / 'file']
    for path in files:
        path.write_bytes(b'x' * 8192)
    (outside / 'ignored').write_bytes(b'x' * 8192)
    (tmp / 'linked-directory').symlink_to(outside, target_is_directory=True)
    (shm / 'linked-file').symlink_to(files[0])
    os.mkfifo(tmp / 'fifo')
    sparse = tmp / 'sparse'
    with sparse.open('wb') as stream:
        stream.truncate(1024 ** 3)
    files.append(sparse)
    expected = sum((path.stat().st_blocks * 512 for path in files))
    assert expected < sparse.stat().st_size
    tree = ast.parse(RUNNER)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {'candidate_disk_bytes', 'watch_disk'}]
    namespace = dict(os=os, errno=errno, stat=stat, kill_candidate=Mock())
    source = ast.unparse(ast.Module(body=functions, type_ignores=[]))
    source = source.replace('256 * 1024 ** 2', str(expected - int(exceeded)))
    exec(compile(source, '<disk-watchdog>', 'exec'), namespace)
    scan = namespace['candidate_disk_bytes']
    roots = (str(tmp), str(shm))
    proc = tmp_path / 'proc'
    proc.mkdir()
    assert scan(roots, proc_root=str(proc)) == expected
    namespace['candidate_disk_bytes'] = lambda **kwargs: scan(roots, proc_root=str(proc))
    stopped = Mock()
    stopped.is_set.side_effect = [False, True]
    status = dict(disk_exceeded=False)
    namespace['watch_disk'](123, stopped, status)
    assert status == dict(disk_exceeded=exceeded)
    if exceeded:
        namespace['kill_candidate'].assert_called_once_with(123)
        stopped.wait.assert_not_called()
    else:
        namespace['kill_candidate'].assert_not_called()
        stopped.wait.assert_called_once_with(0.1)

@pytest.mark.parametrize('race', ['deleted', 'symlink'])
def test_disk_walk_tolerates_directory_races_without_following_symlinks(tmp_path, monkeypatch, race):
    import errno
    import stat
    root, outside = (tmp_path / 'root', tmp_path / 'outside')
    root.mkdir()
    outside.mkdir()
    (outside / 'ignored').write_bytes(b'x' * 8192)
    changing = root / 'changing'
    changing.mkdir()
    original_open = os.open

    def raced_open(path, flags, **kwargs):
        if path == 'changing':
            changing.rmdir()
            if race == 'symlink':
                changing.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, **kwargs)
    monkeypatch.setattr(os, 'open', raced_open)
    function = next((n for n in ast.parse(RUNNER).body if isinstance(n, ast.FunctionDef) and n.name == 'candidate_disk_bytes'))
    namespace = dict(os=os, errno=errno, stat=stat)
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<disk-walk>', 'exec'), namespace)
    proc = tmp_path / 'proc'
    proc.mkdir()
    assert namespace['candidate_disk_bytes']((str(root), str(tmp_path / 'missing')), proc_root=str(proc)) == 0

def test_disk_scan_deduplicates_tree_hardlinks_and_retained_descriptors(tmp_path):
    root, proc, retained = [tmp_path / name for name in ('root', 'proc', 'retained')]
    for path in (root, proc, retained):
        path.mkdir()
    visible, unlinked, memfd, ignored = [directory / name for directory, name in ((root, 'visible'), (retained, 'unlinked'), (retained, 'memfd'), (retained, 'root-owned'))]
    for path in (visible, unlinked, memfd, ignored):
        path.write_bytes(b'x' * 8192)
    os.link(visible, root / 'hardlink')
    for pid, uid in ((11, 65532), (12, 65532), (13, 0)):
        directory = proc / str(pid)
        (directory / 'fd').mkdir(parents=True)
        (directory / 'status').write_text(f'Uid:\t{uid} {uid} {uid} {uid}\n')
        for index, target in enumerate((visible, unlinked, memfd) if uid == 65532 else (ignored,)):
            (directory / 'fd' / str(index)).symlink_to(target)
        (directory / 'fd' / '99').symlink_to(retained / 'closed-fd')
    (proc / '14').mkdir()
    scan = disk_scan_namespace()['candidate_disk_bytes']
    assert scan((str(root),), proc_root=str(proc)) == sum((path.stat().st_blocks * 512 for path in (visible, unlinked, memfd)))

@pytest.mark.parametrize('cancel', [False, True])
def test_disk_scan_stops_at_byte_budget_or_cancellation(tmp_path, monkeypatch, cancel):
    from types import SimpleNamespace
    import stat
    import threading
    root = tmp_path / 'root'
    root.mkdir()
    stopped = threading.Event()
    scanned = []

    class Entries:

        def __iter__(self):
            return self

        def __next__(self):
            scanned.append(1)
            if len(scanned) > 1:
                pytest.fail('scan continued after its budget/cancellation')
            if cancel:
                stopped.set()
            return SimpleNamespace(stat=lambda **kwargs: SimpleNamespace(st_mode=stat.S_IFREG, st_blocks=0 if cancel else (256 * 1024 ** 2 + 512) // 512, st_dev=1, st_ino=1))

        def close(self):
            pass
    monkeypatch.setattr(os, 'scandir', lambda fd: Entries())
    result = disk_scan_namespace()['candidate_disk_bytes']((str(root),), stopped=stopped)
    assert result == (0 if cancel else 256 * 1024 ** 2 + 512)
    assert len(scanned) == 1

@pytest.mark.parametrize('error', [PermissionError, FileNotFoundError, ProcessLookupError])
@pytest.mark.parametrize('entry', ['status', 'fd_directory', 'descriptor', 'tree_directory'])
def test_disk_scan_skips_one_inaccessible_entry_and_continues(tmp_path, monkeypatch, error, entry):
    from types import SimpleNamespace
    root, proc = (tmp_path / 'root', tmp_path / 'proc')
    root.mkdir()
    (root / 'inaccessible').mkdir()
    data = tmp_path / 'data'
    data.write_bytes(b'x' * 8192)
    for pid in ('11', '12'):
        process = proc / pid
        (process / 'fd').mkdir(parents=True)
        (process / 'status').write_text('Uid:\t65532 65532 65532 65532\n')
        for descriptor in ('0', '1'):
            (process / 'fd' / descriptor).symlink_to(data)
    namespace = disk_scan_namespace()
    fake_os = SimpleNamespace(**{name: getattr(os, name) for name in ('path', 'listdir', 'scandir', 'stat', 'open', 'close', 'O_RDONLY', 'O_DIRECTORY', 'O_NOFOLLOW')})
    namespace['os'] = fake_os

    def deny(original, target):

        def operation(path, *args, **kwargs):
            if str(path) == str(target):
                raise error('one unavailable entry')
            return original(path, *args, **kwargs)
        return operation
    if entry == 'status':
        namespace['open'] = deny(open, proc / '11' / 'status')
    elif entry == 'fd_directory':
        monkeypatch.setattr(fake_os, 'scandir', deny(os.scandir, proc / '11' / 'fd'))
    elif entry == 'descriptor':
        monkeypatch.setattr(fake_os, 'stat', deny(os.stat, proc / '11' / 'fd' / '0'))
    else:

        def denied_directory(path, *args, **kwargs):
            if path == 'inaccessible':
                raise error('one unavailable entry')
            return os.open(path, *args, **kwargs)
        monkeypatch.setattr(fake_os, 'open', denied_directory)
    scan = namespace['candidate_disk_bytes']
    assert scan((str(root),), proc_root=str(proc)) == data.stat().st_blocks * 512
    watcher = next((n for n in ast.parse(RUNNER).body if isinstance(n, ast.FunctionDef) and n.name == 'watch_disk'))
    exec(compile(ast.Module(body=[watcher], type_ignores=[]), '<watch>', 'exec'), namespace)
    namespace['candidate_disk_bytes'] = lambda **kwargs: scan((str(root),), proc_root=str(proc))
    from unittest.mock import Mock
    stopped = Mock()
    stopped.is_set.side_effect = [False, True]
    status = {}
    namespace['watch_disk'](99, stopped, status)
    assert status == {}

@pytest.mark.parametrize('watcher', ['watch_memory', 'watch_disk'])
@pytest.mark.parametrize('error', [PermissionError, FileNotFoundError, ProcessLookupError])
def test_watchdog_proc_enumeration_failure_is_fatal(monkeypatch, watcher, error):
    from types import SimpleNamespace
    from unittest.mock import Mock
    namespace = disk_scan_namespace()
    functions = [n for n in ast.parse(RUNNER).body if isinstance(n, ast.FunctionDef) and n.name in {'candidate_rss', 'watch_memory', 'watch_disk'}]
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<watch>', 'exec'), namespace)
    fake_os = SimpleNamespace(listdir=Mock(side_effect=error('cannot enumerate /proc')))
    monkeypatch.setitem(namespace, 'os', fake_os)
    scan = namespace['candidate_disk_bytes']
    namespace['candidate_disk_bytes'] = lambda **kwargs: scan(roots=())
    namespace['kill_candidate'] = Mock()
    stopped = Mock()
    stopped.is_set.return_value = False
    status = {}
    namespace[watcher](99, stopped, status)
    assert status == {'supervisor_error': True}
    fake_os.listdir.assert_called_once_with('/proc')
    namespace['kill_candidate'].assert_called_once_with(99)

def disk_scan_namespace():
    import errno
    import stat
    function = next((n for n in ast.parse(RUNNER).body if isinstance(n, ast.FunctionDef) and n.name == 'candidate_disk_bytes'))
    namespace = dict(os=os, errno=errno, stat=stat, CANDIDATE_UID=65532)
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<disk-scan>', 'exec'), namespace)
    return namespace


@pytest.mark.parametrize('measure', [False, True])
def test_peak_rss_retains_maximum_across_watchdog_samples(measure):
    from unittest.mock import Mock
    watcher = next(n for n in ast.parse(RUNNER).body if isinstance(n, ast.FunctionDef) and n.name == 'watch_memory')
    namespace = dict(candidate_rss=Mock(side_effect=[4096, 8192, 2048]), kill_candidate=Mock())
    exec(compile(ast.Module(body=[watcher], type_ignores=[]), '<watch>', 'exec'), namespace)
    stopped = Mock()
    stopped.is_set.side_effect = [False, False, False, True]
    status = {'peak_candidate_rss_bytes':0} if measure else {}
    namespace['watch_memory'](11, stopped, status)
    assert status == ({'peak_candidate_rss_bytes':8192} if measure else {})
    namespace['kill_candidate'].assert_not_called()
