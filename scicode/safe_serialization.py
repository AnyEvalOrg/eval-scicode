"""Bounded data-only wire format. Never deserialize pickle or evaluate symbols."""
import base64
import io
import json
import math
import re
import numpy as np
import scipy.sparse
import sympy

MAX_BYTES = 32 * 1024 * 1024
MAX_NODES = 100000


def encode(value):
    if isinstance(value, (np.ndarray, np.generic)):
        array = np.asarray(value)
        if array.dtype.hasobject:
            raise ValueError('Object arrays are not supported')
        stream = io.BytesIO()
        np.save(stream, array, allow_pickle=False)
        return ['scalar' if isinstance(value, np.generic) else 'array', base64.b64encode(stream.getvalue()).decode('ascii')]
    if scipy.sparse.issparse(value):
        return ['sparse', value.format, encode(value.toarray())]
    if isinstance(value, sympy.Symbol):
        return ['symbol', str(value)]
    if isinstance(value, sympy.Integer):
        return ['sympy_integer', int(value)]
    if isinstance(value, sympy.Rational):
        return ['sympy_rational', int(value.p), int(value.q)]
    if isinstance(value, sympy.Float):
        return ['sympy_float', str(value), value._prec]
    if type(value) is complex:
        return ['complex', value.real, value.imag]
    if type(value) in (tuple, list):
        return ['tuple' if type(value) is tuple else 'list', [encode(v) for v in value]]
    if isinstance(value, dict):
        return ['dict', [[encode(k), encode(v)] for k,v in value.items()]]
    if value is None or type(value) in (str, bool, int, float):
        return ['plain', value]
    raise ValueError('Unsupported result type')


def decode(node, budget=None, depth=0):
    if budget is None:
        budget = [MAX_NODES, MAX_BYTES]
    budget[0] -= 1
    if budget[0] < 0 or depth > 64 or type(node) is not list or not node:
        raise ValueError('Invalid result')
    kind = node[0]
    if kind in ('array','scalar') and len(node) == 2:
        raw = base64.b64decode(node[1], validate=True)
        budget[1] -= len(raw)
        if budget[1] < 0:
            raise ValueError('Result too large')
        stream = io.BytesIO(raw)
        version = np.lib.format.read_magic(stream)
        if version == (1,0):
            shape, order, dtype = np.lib.format.read_array_header_1_0(stream)
        elif version == (2,0):
            shape, order, dtype = np.lib.format.read_array_header_2_0(stream)
        else:
            raise ValueError('Unsupported npy format')
        # Validate before np.load can allocate based on an attacker-controlled header.
        if (dtype.hasobject or dtype.fields is not None or dtype.kind not in 'biufcSU'
                or math.prod(shape) * dtype.itemsize != len(raw) - stream.tell()
                or math.prod(shape) > MAX_BYTES):
            raise ValueError('Invalid array')
        result = np.load(io.BytesIO(raw), allow_pickle=False)
        if kind == 'scalar':
            if result.shape != ():
                raise ValueError('Invalid scalar')
            return result[()]
        return result
    sub = lambda v: decode(v, budget, depth+1)
    if kind == 'plain' and len(node) == 2 and (node[1] is None or type(node[1]) in (str,bool,int,float)):
        return node[1]
    if kind == 'complex' and len(node) == 3 and all(type(v) in (int,float) for v in node[1:]):
        return complex(*node[1:])
    if kind == 'sympy_integer' and len(node) == 2 and type(node[1]) is int:
        return sympy.Integer(node[1])
    if kind == 'sympy_rational' and len(node) == 3 and all(type(v) is int for v in node[1:]) and node[2] != 0:
        return sympy.Rational(node[1], node[2])
    if (kind == 'sympy_float' and len(node) == 3 and type(node[1]) is str
            and re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?', node[1])
            and type(node[2]) is int and 1 <= node[2] <= 4096):
        return sympy.Float(node[1], precision=node[2])
    if kind == 'symbol' and len(node) == 2 and type(node[1]) is str and len(node[1]) <= 1024:
        return sympy.Symbol(node[1])
    if kind in ('list','tuple','dict') and len(node) == 2 and type(node[1]) is list:
        if kind == 'dict':
            return {sub(k):sub(v) for k,v in node[1]}
        values = [sub(v) for v in node[1]]
        return tuple(values) if kind == 'tuple' else values
    if kind == 'sparse' and len(node) == 3 and node[1] in ('csr','csc','coo','bsr'):
        return getattr(scipy.sparse, node[1]+'_matrix')(sub(node[2]))
    raise ValueError('Invalid result')


def dumps(value):
    data = json.dumps(encode(value), separators=(',',':')).encode()
    if len(data) >= MAX_BYTES:
        raise ValueError('Result too large')
    return data


def loads(data):
    if len(data) >= MAX_BYTES:
        raise ValueError('Result too large')
    return decode(json.loads(data))
