"""Resource-limited trusted executor of UNMODIFIED upstream tests.

No candidate code is evaluated here. Function proxies return plain data over
private pipes; only fixed boolean bytes reach the signing supervisor.
"""
import ctypes
import errno
import json
import os
import resource
import stat
import sys
import sysconfig

ADDRESS_SPACE = 768 * 1024**2
CPU_SECONDS = 300


def prerequisites():
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE, ADDRESS_SPACE))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if os.getuid() != 0 or ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) != 0:
        raise RuntimeError('Protected root worker required')


def trusted_paths():
    # -I -S disables site initialization, including .pth/sitecustomize execution.
    # Check every ancestor so a writable parent cannot replace an import root.
    paths = {os.path.dirname(os.path.abspath(__file__)),
             sysconfig.get_path('purelib'), sysconfig.get_path('platlib')}
    for path in paths:
        current = path
        while True:
            info = os.stat(current, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('Unprotected runtime path')
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    sys.path[:0] = sorted(paths)


def deny_network():
    # Defense in depth in addition to the pod/container network isolation.
    libc = ctypes.CDLL(None)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError('Cannot restrict executor')
    seccomp = ctypes.CDLL('libseccomp.so.2')
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError('Cannot restrict network')
    try:
        for name in ('socket', 'socketpair', 'connect', 'bind', 'listen', 'accept', 'accept4',
                     'sendto', 'sendmsg', 'sendmmsg', 'recvfrom', 'recvmsg', 'recvmmsg',
                     'io_uring_setup'):
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number < 0 or seccomp.seccomp_rule_add(context, 0x50000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError('Cannot restrict network')
        if seccomp.seccomp_load(context) != 0:
            raise RuntimeError('Cannot restrict network')
    finally:
        seccomp.seccomp_release(context)


def execute_tests(request, targets, client):
    import builtins
    import contextlib
    import types
    import numpy as np
    try:
        from . import test_util
        from .proxy_protocol import Proxy
    except ImportError:
        import test_util
        from proxy_protocol import Proxy

    # Preserve upstream import paths without importing the application package
    # (which depends on Inspect and is not part of the isolated runtime).
    package = types.ModuleType('scicode')
    compare = types.ModuleType('scicode.compare')
    package.compare = compare
    compare.cmp = test_util
    sys.modules.update({'scicode': package, 'scicode.compare': compare, 'scicode.compare.cmp': test_util})
    namespace = dict(np=np, cmp_tuple_or_list=test_util.cmp_tuple_or_list,
                     are_dicts_close=test_util.are_dicts_close,
                     are_csc_matrix_close=test_util.are_csc_matrix_close)
    verdicts = []
    # /dev/null avoids buffering unbounded upstream diagnostics in root memory.
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        exec(request.get('dependencies', ''), namespace)
        for name, kind in request['bindings'].items():
            # Test predicates and reference imports must never resolve to a
            # candidate's replacement for all/abs/cmp_tuple_or_list/etc. Reject
            # collisions instead of allowing a proxy to hijack trusted setup.
            if name in namespace or name in vars(builtins):
                raise RuntimeError('Candidate shadows a trusted binding')
            if kind == 'call':
                namespace[name] = Proxy(client, name)
            else:
                binding = client.request('bind', name)
                if (type(binding) is not tuple or len(binding) != 2
                        or binding[0] not in ('call', 'value')):
                    raise RuntimeError('Invalid candidate binding')
                namespace[name] = Proxy(client, name) if binding[0] == 'call' else binding[1]
        for source, target in zip(request['tests'], targets, strict=True):
            client.channel.begin_test()
            namespace['target'] = target
            client.failed = False
            try:
                # Deliberately no AST rewriting, assertion extraction or eval.
                exec(source, namespace)
                verdicts.append(not client.failed)
            except MemoryError:
                raise
            except BaseException:
                verdicts.append(False)
    return verdicts


def main():
    prerequisites()
    trusted_paths()
    deny_network()
    import process_data
    from proxy_protocol import Channel, Client, restore_random_state
    if sys.argv[1] == '--probe':
        import test_util
        return
    with open(sys.argv[1], encoding='utf-8') as stream:
        request = json.load(stream)
    process_data.H5PY_FILE = '/opt/scicode/test_data.h5'
    targets = process_data.process_hdf5_to_tuple(request['step_id'], len(request['tests']))
    channel = Channel(int(sys.argv[2]), int(sys.argv[3]), request['timeout'], request['output_limit'])
    ready = channel.receive()
    if type(ready) is not tuple or len(ready) != 2 or ready[0] is not True:
        raise RuntimeError('Candidate initialization failed')
    restore_random_state(ready[1])
    verdicts = execute_tests(request, targets, Client(channel))
    # Publish only after all tests finish. Crash/MemoryError leaves no successes.
    sys.stdout.buffer.write(bytes(verdicts))


if __name__ == '__main__':
    main()
