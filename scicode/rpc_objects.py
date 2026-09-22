"""Bounded object graphs and opaque handles; no executable deserialization."""
import hashlib

import numpy as np
import scipy.sparse
import sympy

MAX_HANDLES = 4096
MAX_GRAPH_NODES = 100000
MAX_DEPTH = 64


class Handles:
    def __init__(self):
        self.objects = {}
        self.identities = {}

    def put(self, value):
        identity = id(value)
        if identity not in self.identities:
            if len(self.objects) >= MAX_HANDLES:
                raise ValueError('Handle table exhausted')
            key = len(self.objects)
            self.objects[key] = value
            self.identities[identity] = key
        return {'handle': self.identities[identity], 'type_name': type(value).__name__[:128]}

    def get(self, descriptor):
        validate_handle(descriptor)
        return self.objects[descriptor['handle']]


def validate_handle(value):
    if (type(value) is not dict or set(value) != {'handle', 'type_name'}
            or type(value['handle']) is not int or not 0 <= value['handle'] < MAX_HANDLES
            or type(value['type_name']) is not str or len(value['type_name']) > 128):
        raise ValueError('Invalid handle')


class GraphEncoder:
    def __init__(self, remote, unchanged=None):
        self.remote = remote
        self.unchanged = unchanged or {}
        self.nodes = []
        self.objects = []  # Strong references prevent id reuse during encoding.
        self.memo = {}
        self.mutable = []

    def add(self, value, depth=0):
        if id(value) in self.memo:
            return self.memo[id(value)]
        if depth > MAX_DEPTH or len(self.nodes) >= MAX_GRAPH_NODES:
            raise ValueError('Object graph too large')
        index = len(self.nodes)
        self.memo[id(value)] = index
        self.nodes.append(None)
        self.objects.append(value)
        sub = lambda v: self.add(v, depth + 1)
        kind = type(value)
        if kind in (list, dict, np.ndarray):
            self.mutable.append(index)
        if kind in (tuple, list):
            node = ('tuple' if kind is tuple else 'list', [sub(v) for v in value])
        elif kind is dict:
            node = ('dict', [(sub(k), sub(v)) for k, v in value.items()])
        elif kind is slice:
            node = ('slice', [sub(value.start), sub(value.stop), sub(value.step)])
        elif kind is np.ndarray:
            previous = self.unchanged.get(id(value))
            node = (('unchanged', previous[0]) if previous and previous[1] == array_digest(value)
                    else ('array', value))
        elif (value is None or kind in (str, bool, int, float, complex)
              or isinstance(value, (np.generic, sympy.Symbol, sympy.Integer, sympy.Rational, sympy.Float))
              or scipy.sparse.issparse(value)):
            node = ('value', value)
        else:
            node = self.remote(value)
        self.nodes[index] = node
        return index

    def graph(self, root):
        return {'root': self.add(root), 'nodes': self.nodes}


def decode_graph(graph, remote, originals=(), updates=()):
    """Validate graph, restore aliases, then write all argument states in place."""
    if type(graph) is not dict or set(graph) != {'root', 'nodes'}:
        raise ValueError('Invalid graph')
    nodes = graph['nodes']
    if type(nodes) is not list or not 0 < len(nodes) <= MAX_GRAPH_NODES:
        raise ValueError('Invalid graph size')

    def ref(index):
        if type(index) is not int or not 0 <= index < len(nodes):
            raise ValueError('Invalid graph reference')
        return index

    ref(graph['root'])
    if type(updates) not in (list, tuple) or len(updates) != len(originals):
        raise ValueError('Invalid argument updates')
    overrides = {}
    for index, original in zip(updates, originals):
        ref(index)
        if index in overrides:
            raise ValueError('Merged argument identities')
        overrides[index] = original
    # Validate every node before modifying any trusted argument.
    for index, node in enumerate(nodes):
        if type(node) is not tuple or len(node) != 2 or type(node[0]) is not str:
            raise ValueError('Invalid graph node')
        kind, data = node
        if kind in ('list', 'tuple', 'dict', 'slice'):
            if type(data) is not list:
                raise ValueError('Invalid container')
            if kind == 'dict':
                for pair in data:
                    if type(pair) is not tuple or len(pair) != 2:
                        raise ValueError('Invalid dict entry')
                    ref(pair[0])
                    ref(pair[1])
            else:
                if kind == 'slice' and len(data) != 3:
                    raise ValueError('Invalid slice')
                for item in data:
                    ref(item)
        elif kind == 'array':
            if type(data) is not np.ndarray:
                raise ValueError('Invalid array node')
        elif kind == 'unchanged':
            if type(data) is not int or not 0 <= data < len(originals) or type(originals[data]) is not np.ndarray:
                raise ValueError('Invalid unchanged array')
        elif kind == 'handle':
            validate_handle(data)
        elif kind == 'binding':
            if type(data) is not str or len(data) > 1024 or not data.isidentifier():
                raise ValueError('Invalid binding')
        elif kind != 'value':
            raise ValueError('Invalid node kind')
        if index in overrides:
            original = overrides[index]
            expected = {list: 'list', dict: 'dict', np.ndarray: 'array'}[type(original)]
            if kind == 'unchanged':
                if originals[data] is not original:
                    raise ValueError('Changed array identity')
            elif kind != expected:
                raise ValueError('Argument type changed')
            if kind == 'array' and (original.shape != data.shape or original.dtype != data.dtype
                                    or not original.flags.writeable):
                raise ValueError('Array cannot be updated in place')

    objects = dict(overrides)
    for index, (kind, data) in enumerate(nodes):
        if index not in objects and kind in ('list', 'dict'):
            objects[index] = [] if kind == 'list' else {}
    visiting = set()

    def build(index, depth=0):
        if index in objects:
            return objects[index]
        if depth > MAX_DEPTH or index in visiting:
            raise ValueError('Invalid graph depth or immutable cycle')
        visiting.add(index)
        kind, data = nodes[index]
        if kind in ('tuple', 'slice'):
            values = [build(i, depth + 1) for i in data]
            value = tuple(values) if kind == 'tuple' else slice(*values)
        elif kind in ('handle', 'binding'):
            value = remote(kind, data)
        elif kind == 'unchanged':
            value = originals[data]
        else:
            value = data
        objects[index] = value
        visiting.remove(index)
        return value

    for index in range(len(nodes)):
        build(index)
    # Prepare container contents before writes (e.g. reject unhashable keys).
    contents = {}
    for index, (kind, data) in enumerate(nodes):
        if kind == 'list':
            contents[index] = [objects[i] for i in data]
        elif kind == 'dict':
            contents[index] = {objects[k]: objects[v] for k, v in data}
    for index, (kind, data) in enumerate(nodes):
        if kind == 'list':
            objects[index][:] = contents[index]
        elif kind == 'dict':
            objects[index].clear()
            objects[index].update(contents[index])
        elif kind == 'array' and index in overrides:
            np.copyto(objects[index], data, casting='no')
    return objects[graph['root']], [objects[i] for i, n in enumerate(nodes) if n[0] in ('list', 'dict', 'array')]


def array_digest(value):
    """Lossless unchanged-state marker keeps large read-only calls in budget."""
    return value.shape, value.dtype, hashlib.sha256(value.tobytes()).digest()
