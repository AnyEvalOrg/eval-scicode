"""Trusted reference provenance, confined to the comparison worker.

References and derived results carry a wrapper that the RPC client rejects.
Calls in trusted test source go through a local adapter: NumPy (including
asarray, which discards ndarray subclasses) sees ordinary values, but its
results are wrapped again. While references are unwrapped, *all* RPC is denied,
including callbacks from a test helper. No candidate code runs in this scope.
"""
import ast
import builtins
import operator
import types

import numpy as np
import scipy.sparse


class Expected:
    __slots__ = ('tracker', 'value')
    __array_priority__ = 10000

    def __init__(self, tracker, value):
        self.tracker, self.value = tracker, value

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return self.tracker.call(getattr, self, name)

    def __getitem__(self, key):
        return self.tracker.call(operator.getitem, self, key)

    def __iter__(self):
        # Iterator creation/advancement can execute lazy callbacks (map,
        # generators). Keep references confined during those operations too.
        iterator = self.tracker.call(iter, self)
        while True:
            try:
                value = self.tracker.call(next, iterator)
            except StopIteration:
                return
            yield value

    def __bool__(self):
        # Python control flow needs a native bool; explicit bool(...) calls in
        # tests are adapted and their return values retain provenance.
        return self.tracker.call(bool, self).value

    def __array__(self, *args, **kwargs):
        raise TypeError('Reference conversion requires the trusted call adapter')

    def __array_function__(self, func, types, args, kwargs):
        return self.tracker.call(func, *args, **kwargs)

    def __array_ufunc__(self, ufunc, method, *args, **kwargs):
        return self.tracker.call(getattr(ufunc, method), *args, **kwargs)


def _operation(name):
    def operation(self, *args):
        return self.tracker.call(getattr(operator, name), self, *args)
    return operation


for _name in ('eq', 'ne', 'lt', 'le', 'gt', 'ge', 'add', 'sub', 'mul', 'truediv',
              'floordiv', 'mod', 'pow', 'matmul', 'and_', 'or_', 'xor', 'lshift',
              'rshift', 'neg', 'pos', 'abs', 'invert'):
    setattr(Expected, '__' + _name.rstrip('_') + '__', _operation(_name))


def _reflected(name):
    def operation(self, other):
        return self.tracker.call(getattr(operator, name), other, self)
    return operation


for _name in ('add', 'sub', 'mul', 'truediv', 'floordiv', 'mod', 'pow', 'matmul',
              'and_', 'or_', 'xor', 'lshift', 'rshift'):
    setattr(Expected, '__r' + _name.rstrip('_') + '__', _reflected(_name))


class References:
    def __init__(self, client):
        self.client = client
        self.active = 0
        # Keep strong references: no id reuse, and aliases to mutable output
        # buffers stay protected even when NumPy writes through out=.
        self.objects = {}

    def wrap(self, value):
        return value if isinstance(value, Expected) else Expected(self, value)

    def remember(self, value, seen=None):
        if isinstance(value, Expected):
            value = value.value
        if seen is None:
            seen = set()
        if id(value) in seen:
            return
        seen.add(id(value))
        # Immutable values are protected by wrappers, not identity: Python
        # interns bools/ints/strings also used as independent test inputs.
        if isinstance(value, (np.ndarray, list, dict)) or scipy.sparse.issparse(value):
            self.objects[id(value)] = value
        if isinstance(value, np.ndarray) and isinstance(value.base, np.ndarray):
            self.remember(value.base, seen)
        if isinstance(value, (tuple, list)):
            for item in value:
                self.remember(item, seen)
        elif isinstance(value, dict):
            for key, item in value.items():
                self.remember(key, seen)
                self.remember(item, seen)
        elif scipy.sparse.issparse(value):
            for name in ('data', 'indices', 'indptr', 'row', 'col'):
                if hasattr(value, name):
                    self.remember(getattr(value, name), seen)

    def contains(self, value, seen=None):
        if isinstance(value, Expected) or id(value) in self.objects:
            return True
        if isinstance(value, np.ndarray):
            # Views stripped of subclasses/wrappers still alias references.
            base = value.base
            while isinstance(base, np.ndarray):
                if id(base) in self.objects:
                    return True
                base = base.base
        if seen is None:
            seen = set()
        if id(value) in seen:
            return False
        seen.add(id(value))
        if isinstance(value, (types.MethodType, types.BuiltinMethodType)):
            return self.contains(value.__self__, seen)
        if isinstance(value, (list, tuple)):
            return any(self.contains(v, seen) for v in value)
        if isinstance(value, dict):
            return any(self.contains(k, seen) or self.contains(v, seen) for k, v in value.items())
        if isinstance(value, slice):
            return any(self.contains(v, seen) for v in (value.start, value.stop, value.step))
        if isinstance(value, np.ndarray) and value.dtype.hasobject:
            return any(self.contains(v, seen) for v in value.flat)
        if scipy.sparse.issparse(value):
            return any(self.contains(getattr(value, name), seen)
                       for name in ('data', 'indices', 'indptr', 'row', 'col') if hasattr(value, name))
        return False

    def check_call(self, args, kwargs):
        if self.active or self.contains((args, kwargs)):
            raise ValueError('Expected values cannot be sent to the candidate')

    def unwrap(self, value, memo=None):
        if isinstance(value, Expected):
            return value.value
        if memo is None:
            memo = {}
        if id(value) in memo:
            return memo[id(value)]
        if type(value) is list:
            result = []
            memo[id(value)] = result
            result.extend(self.unwrap(v, memo) for v in value)
            return result
        if type(value) is tuple:
            return tuple(self.unwrap(v, memo) for v in value)
        if type(value) is dict:
            result = {}
            memo[id(value)] = result
            result.update((self.unwrap(k, memo), self.unwrap(v, memo)) for k, v in value.items())
            return result
        if type(value) is slice:
            return slice(*(self.unwrap(v, memo) for v in (value.start, value.stop, value.step)))
        return value

    def call(self, func, /, *args, **kwargs):
        try:
            from .proxy_protocol import Proxy
        except ImportError:
            from proxy_protocol import Proxy
        if isinstance(func, Proxy):
            # Never unwrap arguments for a proxied method/function.
            return func(*args, **kwargs)
        if (func is builtins.getattr or func is builtins.hasattr) and args and isinstance(args[0], Proxy):
            # These calls, like obj.attr, are explicit reads in test source.
            if self.contains((args, kwargs)):
                self.client.failed = True
                raise ValueError('Expected values cannot name candidate attributes')
            if kwargs or len(args) not in ((2, 3) if func is builtins.getattr else (2,)):
                raise TypeError('Invalid attribute read')
            try:
                value = self.attribute(*args[:2])
            except AttributeError:
                if func is builtins.hasattr:
                    return False
                if len(args) == 3:
                    return args[2]
                raise
            return True if func is builtins.hasattr else value
        if not self.contains((func, args, kwargs)):
            return func(*args, **kwargs)
        self.active += 1
        try:
            actual = self.unwrap(func)
            code = getattr(actual, '__code__', None)
            if code is not None and code.co_filename == '<trusted-test>':
                result = actual(*args, **kwargs)
            else:
                result = actual(*self.unwrap(args), **self.unwrap(kwargs))
            self.remember(result)
            return self.wrap(result)
        finally:
            # A trusted operation can mutate its input buffers or containers.
            # Protect their aliases even if it raises or returns None.
            self.remember(args)
            self.remember(kwargs)
            self.remember(getattr(actual, '__self__', None))
            self.active -= 1

    def attribute(self, value, name):
        try:
            from .proxy_protocol import Proxy
        except ImportError:
            from proxy_protocol import Proxy
        if isinstance(value, Proxy):
            if name.startswith('__') and name.endswith('__'):
                return getattr(value, name)
            return value._rpc_client.request('getattr', value._rpc_target, (name,))
        if self.contains(value):
            return self.call(getattr, value, name)
        return getattr(value, name)

    def make_slice(self, start, stop, step):
        return self.call(slice, start, stop, step)

    def boolean(self, conjunction, operands):
        tainted = False
        for index, operand in enumerate(operands):
            result = operand()
            tainted |= self.contains(result)
            if index < len(operands) - 1 and bool(result) != conjunction:
                break
        return self.wrap(result) if tainted else result

    def conditional(self, condition, yes, no):
        result = yes() if condition else no()
        return self.wrap(result) if self.contains(condition) else result

    def compare(self, names, operands):
        left = operands[0]()
        tainted = False
        for index, (name, operand) in enumerate(zip(names, operands[1:])):
            right = operand()
            func = ((lambda a, b: a in b) if name == 'in_' else
                    (lambda a, b: a not in b) if name == 'not_in' else getattr(operator, name))
            result = self.call(func, left, right)
            tainted |= self.contains(result)
            if index < len(names) - 1 and not result:
                break
            left = right
        return self.wrap(result) if tainted else result

    def unary(self, name, value):
        return self.call(getattr(operator, name), value)

    def binary(self, name, left, right):
        return self.call(getattr(operator, name), left, right)


# Instrument only trusted test expressions. Assertions, statement order, test
# functions, and candidate calls still execute normally; no assertion splitting
# or candidate source rewriting is involved. The adapter also covers imported
# NumPy aliases and conversions such as float/str/list which erase subclasses.
class ReferenceCalls(ast.NodeTransformer):
    def invoke(self, method, args, keywords=()):
        return ast.Call(ast.Attribute(ast.Name('_scicode_references', ast.Load()), method, ast.Load()),
                        args, list(keywords))

    def visit_Call(self, node):
        self.generic_visit(node)
        return ast.copy_location(self.invoke('call', [node.func, *node.args], node.keywords), node)

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.ctx, ast.Load):
            return ast.copy_location(self.invoke('attribute', [node.value, ast.Constant(node.attr)]), node)
        return node

    def visit_Subscript(self, node):
        self.generic_visit(node)
        if isinstance(node.ctx, ast.Load):
            return ast.copy_location(self.invoke('binary', [ast.Constant('getitem'), node.value, node.slice]), node)
        return node

    def visit_Slice(self, node):
        self.generic_visit(node)
        return ast.copy_location(self.invoke('make_slice',
                                 [v if v is not None else ast.Constant(None)
                                  for v in (node.lower, node.upper, node.step)]), node)

    def lazy(self, values):
        return ast.List([ast.Lambda(ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                                                 kw_defaults=[], defaults=[]), v)
                         for v in values], ast.Load())

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        return ast.copy_location(self.invoke('boolean',
                                 [ast.Constant(isinstance(node.op, ast.And)), self.lazy(node.values)]), node)

    def visit_IfExp(self, node):
        self.generic_visit(node)
        branches = self.lazy([node.body, node.orelse]).elts
        return ast.copy_location(self.invoke('conditional', [node.test, *branches]), node)

    def visit_Compare(self, node):
        self.generic_visit(node)
        names = {ast.Eq: 'eq', ast.NotEq: 'ne', ast.Lt: 'lt', ast.LtE: 'le',
                 ast.Gt: 'gt', ast.GtE: 'ge', ast.Is: 'is_', ast.IsNot: 'is_not',
                 ast.In: 'contains', ast.NotIn: 'contains'}
        # Membership reverses operator.contains operands; implement it with
        # dedicated operations below while preserving chained evaluation.
        ops = ['in_' if isinstance(op, ast.In) else 'not_in' if isinstance(op, ast.NotIn)
               else names[type(op)] for op in node.ops]
        return ast.copy_location(self.invoke('compare',
                                 [ast.List([ast.Constant(n) for n in ops], ast.Load()),
                                  self.lazy([node.left, *node.comparators])]), node)

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        name = {ast.Not: 'not_', ast.USub: 'neg', ast.UAdd: 'pos', ast.Invert: 'invert'}[type(node.op)]
        return ast.copy_location(self.invoke('unary', [ast.Constant(name), node.operand]), node)

    def visit_BinOp(self, node):
        self.generic_visit(node)
        name = {ast.Add: 'add', ast.Sub: 'sub', ast.Mult: 'mul', ast.Div: 'truediv',
                ast.FloorDiv: 'floordiv', ast.Mod: 'mod', ast.Pow: 'pow', ast.MatMult: 'matmul',
                ast.BitAnd: 'and_', ast.BitOr: 'or_', ast.BitXor: 'xor', ast.LShift: 'lshift',
                ast.RShift: 'rshift'}[type(node.op)]
        return ast.copy_location(self.invoke('binary', [ast.Constant(name), node.left, node.right]), node)


def compile_test(source):
    return compile(ast.fix_missing_locations(ReferenceCalls().visit(ast.parse(source))), '<trusted-test>', 'exec')
