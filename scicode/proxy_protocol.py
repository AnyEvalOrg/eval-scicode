"""Length-framed, bounded data-only RPC. This module never evaluates source."""
import os
import select
import struct
import time

try:
    from .safe_serialization import MAX_BYTES, dumps, loads
except ImportError:
    from safe_serialization import MAX_BYTES, dumps, loads


class FailedCall(RuntimeError):
    pass


class Channel:
    def __init__(self, read_fd, write_fd, timeout, limit=MAX_BYTES):
        self.read_fd, self.write_fd = read_fd, write_fd
        self.deadline = time.monotonic() + timeout
        # Per-test aggregate reply budget; each frame is separately below MAX_BYTES.
        self.limit = limit
        self.remaining = self.limit
        self.broken = False

    def begin_test(self):
        # Bound aggregate traffic within each trusted test, not across all
        # tests in a step. The step-wide deadline is deliberately unchanged.
        self.remaining = self.limit

    def _wait(self, fd, writing=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise FailedCall('Call deadline exceeded')
        ready = select.select([] if writing else [fd], [fd] if writing else [], [], remaining)
        if not any(ready):
            raise FailedCall('Call deadline exceeded')

    def send(self, value):
        if self.broken:
            raise FailedCall('Channel is broken')
        data = dumps(value)
        if len(data) >= MAX_BYTES:
            raise FailedCall('Request too large')
        data = memoryview(struct.pack('!Q', len(data)) + data)
        while data:
            self._wait(self.write_fd, True)
            # A PIPE_BUF-sized write cannot block after select says writable.
            count = os.write(self.write_fd, data[:4096])
            data = data[count:]

    def _read(self, count):
        # Read in place: one buffer of the (already budget-checked) frame size,
        # instead of a growing buffer plus a full copy (halves peak memory).
        buffer = bytearray(count)
        view = memoryview(buffer)
        received = 0
        try:
            while received < count:
                self._wait(self.read_fd)
                size = os.readv(self.read_fd, [view[received:received + 65536]])
                if not size:
                    raise FailedCall('Candidate disconnected')
                received += size
        finally:
            view.release()
        return buffer

    def receive(self):
        if self.broken:
            raise FailedCall('Channel is broken')
        try:
            size, = struct.unpack('!Q', self._read(8))
            if size >= min(self.remaining, MAX_BYTES):
                raise FailedCall('Reply budget exceeded')
            self.remaining -= size
            return loads(self._read(size))
        except BaseException:
            # A rejected/partial frame cannot be resynchronized at the next
            # test boundary, even though that test receives a fresh budget.
            self.broken = True
            raise


def random_state():
    import random
    import numpy as np
    return np.random.get_state(), random.getstate()


def restore_random_state(state):
    """Validate data-only PRNG states before updating either trusted generator."""
    import random
    import numpy as np
    if type(state) is not tuple or len(state) != 2:
        raise ValueError('Invalid random state')
    numpy_state, python_state = state
    if (type(numpy_state) is not tuple or len(numpy_state) != 5
            or type(numpy_state[0]) is not str or numpy_state[0] != 'MT19937'
            or type(numpy_state[1]) is not np.ndarray
            or numpy_state[1].dtype != np.dtype('uint32') or numpy_state[1].shape != (624,)
            or type(numpy_state[2]) is not int or not 0 <= numpy_state[2] <= 624
            or type(numpy_state[3]) is not int or numpy_state[3] not in (0, 1)
            or type(numpy_state[4]) is not float):
        raise ValueError('Invalid NumPy random state')
    if (type(python_state) is not tuple or len(python_state) != 3
            or type(python_state[0]) is not int or python_state[0] != 3
            or type(python_state[1]) is not tuple or len(python_state[1]) != 625
            or any(type(v) is not int or not 0 <= v < 2**32 for v in python_state[1][:-1])
            or type(python_state[1][-1]) is not int or not 0 <= python_state[1][-1] <= 624
            or (python_state[2] is not None and type(python_state[2]) is not float)):
        raise ValueError('Invalid Python random state')
    np.random.set_state(numpy_state)
    random.setstate(python_state)


class Proxy:
    """Local facade; only data and opaque references cross the pipe."""
    __slots__ = ('_rpc_client', '_rpc_target')

    def __init__(self, client, target):
        object.__setattr__(self, '_rpc_client', client)
        object.__setattr__(self, '_rpc_target', target)

    def __call__(self, *args, **kwargs):
        return self._rpc_client.request('call', self._rpc_target, args, kwargs)

    def __getattr__(self, name):
        # Library protocol discovery is not an explicit object attribute read.
        # In particular, never expose NumPy's native pointer protocols.
        # Explicit reads in test source use References.attribute. A library
        # probing .dtype/.shape/.real must not turn comparison into RPC.
        raise AttributeError(name)

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other

    __hash__ = object.__hash__

    def __bool__(self):
        return False

    def _unsupported(self, *args, **kwargs):
        self._rpc_client.failed = True
        raise FailedCall('Opaque candidate handles do not support value protocols')

    __array__ = __float__ = __int__ = __index__ = __complex__ = _unsupported
    __len__ = __iter__ = __contains__ = _unsupported
    __lt__ = __le__ = __gt__ = __ge__ = _unsupported
    __setattr__ = __getitem__ = __setitem__ = __delitem__ = _unsupported

    # These are deliberately local failures, including reflected and in-place
    # arithmetic. A failed conversion cannot be caught into a passing test.


for _name in (
    '__neg__', '__pos__', '__abs__', '__invert__', '__round__', '__floor__', '__ceil__',
    '__trunc__', '__add__', '__sub__', '__mul__', '__truediv__', '__floordiv__', '__mod__',
    '__divmod__', '__pow__', '__matmul__', '__and__', '__or__', '__xor__',
    '__lshift__', '__rshift__', '__radd__', '__rsub__', '__rmul__', '__rtruediv__',
    '__rfloordiv__', '__rmod__', '__rdivmod__', '__rpow__', '__rmatmul__',
    '__rand__', '__ror__', '__rxor__', '__rlshift__', '__rrshift__',
    '__iadd__', '__isub__', '__imul__', '__itruediv__', '__ifloordiv__', '__imod__',
    '__ipow__', '__imatmul__', '__iand__', '__ior__', '__ixor__', '__ilshift__', '__irshift__',
):
    setattr(Proxy, _name, Proxy._unsupported)


class _RemoteMissing(Exception):
    pass


class Client:
    def __init__(self, channel):
        self.channel, self.failed = channel, False
        self.handles = {}
        self.expected = None

    def _pack_remote(self, value):
        if type(value) is not Proxy or value._rpc_client is not self:
            raise ValueError('Unsupported trusted argument')
        return value._rpc_target

    def _remote(self, kind, data):
        if kind != 'handle':
            raise ValueError('Candidate returned a binding reference')
        key = data['handle']
        if key not in self.handles:
            self.handles[key] = Proxy(self, (kind, data))
        elif self.handles[key]._rpc_target[1] != data:
            raise ValueError('Handle type changed')
        return self.handles[key]

    def request(self, operation, target, args=(), kwargs=None):
        try:
            try:
                from .rpc_objects import GraphEncoder, decode_graph
            except ImportError:
                from rpc_objects import GraphEncoder, decode_graph
            if operation not in ('call', 'getattr', 'bind'):
                raise FailedCall('Unsupported proxy operation')
            if self.expected is not None:
                self.expected.check_call(args, kwargs)
            encoder = GraphEncoder(self._pack_remote)
            graph = encoder.graph((args, {} if kwargs is None else kwargs))
            originals = [encoder.objects[i] for i in encoder.mutable]
            self.channel.send((operation, target, graph, random_state()))
            reply = self.channel.receive()
            if (type(reply) is not dict or set(reply) != {'status', 'graph', 'updates', 'state'}
                    or reply['status'] not in ('ok', 'missing', 'error')):
                raise FailedCall('Invalid candidate reply')
            value, _ = decode_graph(reply['graph'], self._remote, originals, reply['updates'])
            restore_random_state(reply['state'])
            if reply['status'] == 'missing' and operation == 'getattr':
                raise _RemoteMissing(args[0])
            if reply['status'] != 'ok':
                raise FailedCall('Candidate call failed')
            return value
        except _RemoteMissing as exc:
            raise AttributeError(*exc.args) from None
        except MemoryError:
            raise
        except BaseException as exc:
            self.failed = True
            raise FailedCall('Candidate call failed') from exc
