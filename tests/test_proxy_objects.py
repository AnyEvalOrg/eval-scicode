"""Object protocol/identity regressions through the actual signed subprocess path."""
from pathlib import Path

import numpy as np
import pytest

from proxy_harness import run_signed
from test_proxy_execution import packaged


def test_recursive_inventory_is_signed_incorrect(tmp_path):
    # Valid Python; ast.parse succeeds but recursive NodeVisitor overflows.
    source = 'x=' + '+'.join(['1'] * 1001)
    result = run_signed(tmp_path, source, ['assert True'], [None])
    assert result['verdicts'] == [False]


def test_opaque_objects_explicit_attributes_methods_and_calls(tmp_path):
    code = """import numpy as np
class Number:
    def __init__(self, value): self.value = value
    def change(self, value): self.value = value
    def __call__(self, x): return self.value * x
    def array(self): return np.array([self.value])
def factory(): return Number(4)
def closure(x): return lambda y: x + y
def identity(obj): return obj
def pair():
    obj = Number(3)
    return obj, obj
"""
    source = """n = factory()
assert n.value == 4 and n(3) == 12
n.change(8)
assert n.value == 8 and np.array_equal(n.array(), [8])
assert identity(n) is n
assert n == n and not (n != n)
a, b = pair()
assert a is b and a == b
assert a != n and not (a == n)
assert closure(5)(6) == 11
"""
    assert run_signed(tmp_path, code, [source], [None])['verdicts'] == [True]


def test_aliases_nested_mutation_cycles_and_repeated_calls(tmp_path):
    code = '''def change(a, again, mapping, *, keyword):
    assert a is again is mapping['array'] is keyword
    assert mapping['list'][0] is a
    a += 2
    mapping['list'].append(a)
    mapping['new'] = mapping['list']
    return a, mapping['list']
def cycle(value):
    assert value[0] is value
    value.append(42)
    return value
def detach(value):
    old = value.pop('list')
    old.append(99)
'''
    sources = ['''a = np.arange(3.)
original = a
items = [a]
mapping = {'array': a, 'list': items}
x, y = change(a, a, mapping, keyword=a)
assert x is a is original and y is items is mapping['new']
assert items[0] is items[1] is a
assert np.array_equal(a, [2, 3, 4])
''', '''change(a, a, mapping, keyword=a)
assert np.array_equal(original, [4, 5, 6]) and len(items) == 3
assert items[2] is a
recursive = []
recursive.append(recursive)
assert cycle(recursive) is recursive
assert recursive[0] is recursive and recursive[1] == 42
detach(mapping)
assert 'list' not in mapping and items[-1] == 99
''']
    assert run_signed(tmp_path, code, sources, [None]*2)['verdicts'] == [True]*2


@pytest.mark.parametrize('change', ['a.resize((4,), refcheck=False)', "a.dtype = np.int32"])
def test_unwritable_array_change_fails_even_if_caught(tmp_path, change):
    code = 'import numpy as np\ndef change(a):\n    ' + change
    source = 'a = np.zeros(2)\ntry:\n    change(a)\nexcept Exception:\n    pass\nassert True'
    assert run_signed(tmp_path, code, [source], [None])['verdicts'] == [False]


def test_handle_limit_is_signed_incorrect(tmp_path):
    code = 'def many(): return [object() for _ in range(4097)]'
    assert run_signed(tmp_path, code, ['many()'], [None])['verdicts'] == [False]


def test_candidate_cannot_supply_native_array_pointers(tmp_path):
    code = '''class Unsafe:
    __array_interface__ = {'version': 3, 'shape': (1,), 'typestr': '<f8', 'data': (1, False)}
    __array_struct__ = 1
    def __array__(self, dtype=None): return self.__array_interface__
def candidate(): return Unsafe()
'''
    source = 'try:\n    np.asarray(candidate())\nexcept Exception:\n    pass\nassert True'
    assert run_signed(tmp_path, code, [source], [None])['verdicts'] == [False]


SLATER_REFERENCE = '''import numpy as np
class Slater:
    def __init__(self, alpha): self.alpha = alpha
    def value(self, configs):
        return np.exp(-self.alpha * np.linalg.norm(configs, axis=2).sum(axis=1))
    def gradient(self, configs):
        return -self.alpha * configs / np.linalg.norm(configs, axis=2)[..., None]
    def laplacian(self, configs):
        radii = np.linalg.norm(configs, axis=2)
        return self.alpha**2 - 2*self.alpha/radii
    def kinetic(self, configs):
        return -0.5 * self.laplacian(configs).sum(axis=1)
'''


@pytest.mark.parametrize('step_id', ['30.1', '46.1', '68.1'])
def test_upstream_slater_classes(tmp_path, step_id):
    record, step = packaged(step_id)
    # These are test-split steps: no ground truth is packaged. Independently
    # implement exp(-alpha*(r1+r2)) and use the real canonical HDF5 targets.
    from scicode import process_data
    process_data.H5PY_FILE = str(Path('scicode/test_data.h5').resolve())
    targets = process_data.process_hdf5_to_tuple(step_id, len(step['test_cases']))
    assert run_signed(tmp_path, SLATER_REFERENCE, step['test_cases'], targets,
                      dependencies=record['required_dependencies'])['verdicts'] == [True]*3


def test_upstream_maxwell_object_retains_state_across_calls(tmp_path):
    record, step = packaged('13.12')
    code = Path('tests/fixtures/maxwell_reference.py').read_text()
    reference = {}
    exec(code, reference)
    targets = []
    x, y, z = np.meshgrid(*[np.linspace(0, 2, 50)]*3)
    for potential in ((x, y, z), (-y, x, z*0), (x*0, -z, y)):
        maxwell = reference['Maxwell'](50, 2)
        reference['stepper'](maxwell, (x, y, z, *potential, z*0+1), 0.5, 0.2)
        # Independent central divergence calculation checks the reference
        # constraint, as well as furnishing a direct-process transport oracle.
        delta = maxwell.delta
        ex, ey, ez = maxwell.E_x, maxwell.E_y, maxwell.E_z
        divergence = ((ex[2:, 1:-1, 1:-1] - ex[:-2, 1:-1, 1:-1])
                      + (ey[1:-1, 2:, 1:-1] - ey[1:-1, :-2, 1:-1])
                      + (ez[1:-1, 1:-1, 2:] - ez[1:-1, 1:-1, :-2])) / (2*delta)
        expected = np.sqrt(np.sum(divergence**2) * delta**3)
        assert np.isclose(reference['check_constraint'](maxwell), expected)
        assert np.isclose(maxwell.t, 0.2) and expected > 0
        targets.append(expected)
    result = run_signed(tmp_path, code, step['test_cases'], targets,
                        dependencies=record['required_dependencies'], timeout=60)
    assert result['verdicts'] == [True]*3


@pytest.mark.parametrize('graph', [
    {'root': -1, 'nodes': [('value', 0)]},
    {'root': 0, 'nodes': [('handle', {'handle': 4096, 'type_name': 'Bad'})]},
    {'root': 0, 'nodes': [('handle', {'handle': True, 'type_name': 'Bad'})]},
    {'root': 0, 'nodes': [('binding', 'eval')]},
    {'root': 0, 'nodes': [('unchanged', 10000)]},
    {'root': 0, 'nodes': [('tuple', [0])]},
])
def test_invalid_object_graph_is_signed_incorrect(tmp_path, graph):
    code = ('def candidate():\n    import rpc_objects\n'
            '    rpc_objects.GraphEncoder.graph = lambda *a: ' + repr(graph) + '\n    return 1')
    source = 'try:\n    candidate()\nexcept Exception:\n    pass\nassert True'
    assert run_signed(tmp_path, code, [source], [None])['verdicts'] == [False]
