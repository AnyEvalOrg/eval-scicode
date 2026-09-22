"""Root comparison process: no signing key, candidate code, or inherited fds.

All result file reads, wire decoding and numerical predicates happen here.
The supervisor imposes a wall deadline in addition to these hard limits.
"""
import ctypes
import json
import os
import resource
import stat
import sys

ADDRESS_SPACE = 768 * 1024**2
CPU_SECONDS = 8


def prerequisites():
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE, ADDRESS_SPACE))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if os.getuid() != 0 or ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) != 0:
        raise RuntimeError('Protected root worker required')


def compare_results(request, candidate_work):
    from safe_serialization import loads
    from test_plan import compare_plan
    import process_data

    process_data.H5PY_FILE = '/opt/scicode/test_data.h5'
    plans = request['plans']
    targets = process_data.process_hdf5_to_tuple(request['step_id'], len(plans))
    # First byte preserves the aggregate overflow status; the rest are verdicts.
    verdicts = bytearray(len(plans) + 1)
    total_bytes = 0
    for index, plan in enumerate(plans):
        try:
            path = os.path.join(candidate_work, 'result-' + str(index))
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != request['candidate_uid']
                        or info.st_nlink != 1):
                    raise ValueError('Invalid result file')
                data = stream.read(max(0, request['output_limit'] - total_bytes) + 1)
            total_bytes += len(data)
            if total_bytes >= request['output_limit']:
                verdicts[0] = 1
                break
            verdicts[index + 1] = bool(compare_plan(plan, loads(data), targets[index]))
        except MemoryError:
            raise
        except Exception:
            pass
    return verdicts


def main():
    prerequisites()
    # -I excludes the script directory; only this root-owned runtime is added.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import safe_serialization
    import test_plan
    import test_util
    import process_data
    if sys.argv[1] == '--probe':
        return
    with open(sys.argv[1], encoding='utf-8') as stream:
        request = json.load(stream)
    # Only fixed, worker-authored boolean bytes reach the signing supervisor.
    # Publish atomically after all comparisons: failure leaves no success bytes.
    verdicts = compare_results(request, sys.argv[2])
    sys.stdout.buffer.write(verdicts)


if __name__ == '__main__':
    main()
