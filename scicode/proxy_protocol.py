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
        self.limit = min(limit, MAX_BYTES)
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
        chunks = bytearray()
        while len(chunks) < count:
            self._wait(self.read_fd)
            chunk = os.read(self.read_fd, min(65536, count - len(chunks)))
            if not chunk:
                raise FailedCall('Candidate disconnected')
            chunks.extend(chunk)
        return bytes(chunks)

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
    def __init__(self, client, name):
        self.client, self.name = client, name

    def __call__(self, *args, **kwargs):
        return self.client.request('call', self.name, pack_arguments(args), pack_arguments(kwargs))


def pack_arguments(value):
    # Only trusted-created proxy references travel in this direction. Returned
    # callable/object values are always rejected by the ordinary wire decoder.
    if type(value) is Proxy:
        return ('ref', value.name)
    if type(value) in (list, tuple):
        return ('tuple' if type(value) is tuple else 'list', [pack_arguments(v) for v in value])
    if type(value) is dict:
        return ('dict', [(pack_arguments(k), pack_arguments(v)) for k, v in value.items()])
    return ('value', value)


def unpack_arguments(value, namespace):
    kind, data = value
    if kind == 'ref':
        return namespace[data]
    if kind == 'tuple':
        return tuple(unpack_arguments(v, namespace) for v in data)
    if kind == 'list':
        return [unpack_arguments(v, namespace) for v in data]
    if kind == 'dict':
        return {unpack_arguments(k, namespace): unpack_arguments(v, namespace) for k, v in data}
    if kind == 'value':
        return data
    raise ValueError('Invalid argument')


class Client:
    def __init__(self, channel):
        self.channel, self.failed = channel, False

    def request(self, operation, name, args=None, kwargs=None):
        try:
            self.channel.send((operation, name, args, kwargs,
                               random_state() if operation == 'call' else None))
            reply = self.channel.receive()
            if type(reply) is not tuple or len(reply) != 2 or reply[0] is not True:
                raise FailedCall('Candidate call failed')
            value = reply[1]
            if operation == 'call':
                if type(value) is not tuple or len(value) != 2:
                    raise FailedCall('Invalid call result')
                value, state = value
                restore_random_state(state)
            return value
        except MemoryError:
            raise
        except BaseException as exc:
            self.failed = True
            raise FailedCall('Candidate call failed') from exc
