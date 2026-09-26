"""Resource-limited trusted executor of upstream tests with reference provenance.

No candidate code is evaluated here. Object proxies return data/opaque IDs over
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

# Measured in-image VmPeak: 996.5 MiB for the largest reply (63.2 case 2), 309 MiB for
# every dev reference step. 1280 MiB (1.28x) fits the 6 GiB pod budget in values.yaml.
ADDRESS_SPACE = 1280 * 1024**2
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
        from .expected_values import References, compile_test
    except ImportError:
        import test_util
        from proxy_protocol import Proxy
        from expected_values import References, compile_test

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
    references = References(client)
    client.expected = references
    namespace['_scicode_references'] = references
    verdicts = []
    # /dev/null avoids buffering unbounded upstream diagnostics in root memory.
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        exec(request.get('dependencies', ''), namespace)
        import ast
        referenced = {n.id for source in request['tests'] for n in ast.walk(ast.parse(source))
                      if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        for name, kind in client.bindings.items():
            if kind != 'call' and name not in referenced:
                continue
            # Test predicates and reference imports must never resolve to a
            # candidate's replacement for all/abs/cmp_tuple_or_list/etc. Reject
            # collisions instead of allowing a proxy to hijack trusted setup.
            if name in namespace or name in vars(builtins):
                raise RuntimeError('Candidate shadows a trusted binding')
            namespace[name] = (Proxy(client, ('binding', name)) if kind == 'call'
                               else client.request('bind', ('binding', name)))
        for source, target in zip(request['tests'], targets, strict=True):
            client.channel.begin_test()
            references.remember(target)
            namespace['target'] = references.wrap(target)
            client.failed = False
            try:
                exec(compile_test(source), namespace)
                verdicts.append(not client.failed)
            except MemoryError:
                raise
            except BaseException:
                verdicts.append(False)
    return verdicts


def peak_virtual_bytes():
    try:
        with open('/proc/self/status', encoding='ascii') as stream:
            for line in stream:
                if line.startswith('VmPeak:'):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


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
    channel = Channel(int(sys.argv[2]), int(sys.argv[3]), request['timeout'], request['reply_limit'])
    ready = channel.receive()
    if type(ready) is not tuple or len(ready) != 3 or ready[0] is not True:
        raise RuntimeError('Candidate initialization failed')
    restore_random_state(ready[1])
    bindings = ready[2]
    if (type(bindings) is not dict or len(bindings) > 4096
            or any(type(name) is not str or len(name) > 1024 or not name.isidentifier()
                   or type(kind) is not str or kind not in ('call', 'get')
                   for name, kind in bindings.items())):
        raise RuntimeError('Invalid binding inventory')
    client = Client(channel)
    client.bindings = bindings
    verdicts = execute_tests(request, targets, client)
    # Publish only after all tests finish. Crash/MemoryError leaves no successes.
    output = bytes(verdicts)
    if request.get('measure_peak_rss') is True:
        # Operator measurement only: the executor's peak virtual size (VmPeak), which
        # RLIMIT_AS bounds, as 8 big-endian bytes after the verdicts.
        output += peak_virtual_bytes().to_bytes(8, 'big')
    sys.stdout.buffer.write(output)


if __name__ == '__main__':
    main()
