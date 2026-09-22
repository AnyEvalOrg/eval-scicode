from pathlib import Path

import numpy as np
import pytest
from scicode.dataset import load_records
from scicode.bindings import candidate_bindings
from scicode.receipts import receipt_failure
from scicode.safe_serialization import dumps, loads
from proxy_harness import run_signed


def packaged(step_id):
    for record in load_records(True):
        for step in record['sub_steps']:
            if step['step_number'] == step_id:
                return record, step
    raise AssertionError(step_id)


@pytest.mark.parametrize('body,passed', [
    ('return 7.', True), ('return 9.', False), ('return lambda: 7.', False),
    ('return object()', False), ('raise ValueError("PRIVATE")', False),
    ('return np.zeros(5_000_000)', False),
    ('return "x" * (34 * 1024**2)', False),
    ('np.allclose = lambda *a, **k: True; return 9.', False),
    ('import os; os._exit(0)', False),
    ('import time; time.sleep(60)', False),
])
def test_signed_results_reject_bad_calls(tmp_path, body, passed):
    result = run_signed(tmp_path, 'import numpy as np\ndef candidate():\n    '+body,
                        ['assert np.allclose(candidate(), target)'], [7.], timeout=3 if 'sleep(60)' in body else 10)
    assert result['verdicts'] == [passed]
    assert (receipt_failure(result) is None) is passed


@pytest.mark.parametrize('payload', [
    b'\x00'*7 + b'\x01' + b'x',
    (2**63).to_bytes(8, 'big'),
    len(b'["pickle","bad"]').to_bytes(8, 'big') + b'["pickle","bad"]',
    len(b'["plain",true]').to_bytes(8, 'big') + b'["plain",true]',
])
def test_malformed_pipe_replies_are_signed_incorrect(tmp_path, payload):
    code = 'import os, sys\ndef candidate():\n    os.write(int(sys.argv[3]), '+repr(payload)+')\n    os._exit(0)'
    result = run_signed(tmp_path, code, ['assert candidate() == target'], [True])
    assert result['verdicts'] == [False] and receipt_failure(result)


@pytest.mark.parametrize('fault', [
    ('comparison_worker', 'execute_tests', 'raise MemoryError()'),
    ('comparison_worker', 'execute_tests', 'import os; os._exit(139)'),
    ('comparison_worker', 'execute_tests', 'import time; time.sleep(60)'),
    ('safe_serialization', 'loads', 'while True: pass'),
    ('safe_serialization', 'loads', 'raise MemoryError()'),
])
def test_executor_fault_is_signed_incorrect(tmp_path, fault):
    result = run_signed(tmp_path, 'def candidate(): return 7.',
                        ['assert candidate() == target'], [7.], fault=fault, timeout=4)
    assert result['verdicts'] == [False] and receipt_failure(result)


@pytest.mark.parametrize('step_id,name,helper,bad', [
    ('72.1','neighbor_list','test_neighbor','[(0, 0)] * 4'),
    ('72.2','energy_site','test_energy_site','999'),
    ('72.3','energy','test_energy','999'),
    ('72.5','get_flip_probability_magnetization','test_spin_flip','(999, 999)'),
])
def test_packaged_test_helpers_cannot_be_forged(tmp_path, step_id, name, helper, bad):
    _, step = packaged(step_id)
    code = f'def {name}(*args, **kwargs): return {bad}\ndef {helper}(): return True'
    result = run_signed(tmp_path, code, [step['test_cases'][-1]], [True])
    assert result['verdicts'] == [False]


def test_synthetic_incorrect_matrix_cannot_forge_helper(tmp_path):
    source = '''def check_matrix():
    actual = matrix()
    expected = np.eye(3)
    return np.allclose(actual, expected)
assert check_matrix() == target'''
    code = 'import numpy as np\ndef matrix(): return np.zeros((3, 3))\ndef check_matrix(): return True'
    assert run_signed(tmp_path, code, [source], [True])['verdicts'] == [False]


@pytest.mark.parametrize('step_id,name', [('61.5','get_hkl'), ('73.9','auto_index')])
@pytest.mark.parametrize('correct', [True, False])
def test_packaged_chained_numpy_predicates(tmp_path, step_id, name, correct):
    _, step = packaged(step_id)
    # Reference fixture for this predicate: integral indices satisfy its check;
    # fractional indices do not. Tests retain their original chained call.
    code = f'import numpy as np\ndef {name}(*args): return np.array([1., 2., {3. if correct else 3.3}])'
    assert run_signed(tmp_path, code, [step['test_cases'][-1]], [True])['verdicts'] == [correct]


@pytest.mark.parametrize('correct', [True, False])
def test_78_2_reference_and_wrong_implementation(tmp_path, correct):
    record, step = packaged('78.2')
    code = '\n'.join([record['required_dependencies'], *(s['ground_truth_code'] for s in record['sub_steps'][:2])])
    if not correct:
        code += '\ndef runge_kutta_4th_order(*args): return np.full((1001, 2), np.nan)'
    result = run_signed(tmp_path, code, step['test_cases'], [None]*3)
    assert result['verdicts'] == [correct]*3


def test_52_4_loop_values_and_test_sequencing(tmp_path):
    _, step = packaged('52.4')
    # Each loop iteration returns a pair; y0 intentionally persists from test 1.
    code = 'def FindBoundStates(y0, R, l, n, E): return [(l, -1.)]'
    targets = [np.array([(l, -1.) for l in range(6)]),
               np.array([(l, -1.) for l in range(4)]), np.array([True])]
    result = run_signed(tmp_path, code, step['test_cases'], targets)
    assert result['verdicts'] == [True]*3


def test_constants_recursion_keywords_sparse_and_shared_candidate_state(tmp_path):
    code = '''import scipy.sparse as sp
constant = 3
calls = 0
def factorial(n):
    return 1 if n == 0 else n * factorial(n - 1)
def candidate(value, *, scale):
    global calls
    calls += 1
    return value * scale, calls, factorial(5)
'''
    tests = ['value, count, fac = candidate(sp.csr_matrix([[constant, 0]]), scale=2)\nassert value[0,0] == 6 and count == 1 and fac == 120',
             'value, count, fac = candidate(value, scale=3)\nassert value[0,0] == 18 and count == 2']
    result = run_signed(tmp_path, code, tests, [None]*2, dependencies='import scipy.sparse as sp')
    assert result['verdicts'] == [True]*2


def test_failed_call_cannot_be_caught_into_pass(tmp_path):
    source = 'try:\n    candidate()\nexcept Exception:\n    pass\nassert True'
    assert run_signed(tmp_path, 'def candidate(): return object()', [source], [None])['verdicts'] == [False]


def test_inventory_never_executes_candidate_and_omits_unused_globals():
    code = 'raise RuntimeError()\nunused = 1\nconstant = 2\nclass Foo: pass\ndef f(): pass'
    assert candidate_bindings(code, ['assert constant == 2\nFoo()']) == {'constant':'get','Foo':'call','f':'call'}


@pytest.mark.parametrize('record,step', [(r,s) for r in load_records(dev_only=True) for s in r['sub_steps']],
                         ids=lambda item: item.get('step_number', item.get('problem_id')))
def test_local_dev_ground_truth(tmp_path, record, step):
    import ast
    target_path = Path('scicode/test_data.h5')
    needs_target = any(isinstance(n, ast.Name) and n.id == 'target'
                       for source in step['test_cases'] for n in ast.walk(ast.parse(source)))
    if needs_target and not target_path.exists():
        pytest.skip('Canonical HDF5 targets not available locally')
    if target_path.exists():
        from scicode import process_data
        process_data.H5PY_FILE = str(target_path)
        targets = process_data.process_hdf5_to_tuple(step['step_number'], len(step['test_cases']))
    else:
        targets = [None] * len(step['test_cases'])
    index = record['sub_steps'].index(step)
    code = '\n'.join([record['required_dependencies'], 'from test_util import are_dicts_close, cmp_tuple_or_list',
                      *(s['ground_truth_code'] for s in record['sub_steps'][:index+1])])
    result = run_signed(tmp_path, code, step['test_cases'], targets,
                        dependencies=record['required_dependencies'], timeout=300)
    assert receipt_failure(result) is None, result


def test_supervisor_never_decodes_candidate_bytes(tmp_path, monkeypatch):
    import scicode.safe_serialization as wire
    monkeypatch.setattr(wire, 'loads', lambda *a: (_ for _ in ()).throw(AssertionError('Signing process decoded bytes')))
    result = run_signed(tmp_path, 'def candidate(): return 7.', ['assert candidate() == target'], [7.])
    assert result['verdicts'] == [True], result


def test_aggregate_reply_budget_is_signed_incorrect(tmp_path):
    code = 'def candidate(): return "x" * 40000'
    result = run_signed(tmp_path, code, ['for i in range(5):\n    assert len(candidate()) == 40000'], [None], output_limit=65536)
    assert result['verdicts'] == [False] and receipt_failure(result)


def test_reply_budget_is_per_test_with_multiple_calls(tmp_path):
    # Each test fits the budget, but their combined replies exceed it. Two
    # calls in a case exercise the final cases of both image-filter steps.
    code = 'def candidate(): return "x" * 10000'
    source = 'for i in range(2):\n    assert candidate() == "x" * 10000'
    result = run_signed(tmp_path, code, [source]*3, [None]*3, output_limit=65536)
    assert result['verdicts'] == [True]*3, result


def test_large_reference_sets_do_not_travel_in_argv_or_one_frame(tmp_path):
    # Each target is below MAX_BYTES; the set exceeds it (and OS argv limits).
    target = np.zeros(1_100_000)
    source = 'assert candidate() == 1 and target.shape == (1100000,) and not target.any()'
    result = run_signed(tmp_path, 'def candidate(): return 1', [source]*3, [target]*3)
    assert result['verdicts'] == [True]*3, result


def test_random_streams_match_single_process_across_calls_and_tests(tmp_path):
    code = '''import numpy as np
import random
np.random.seed(3)
random.seed(5)
def draw():
    return (np.random.randint(0, 10), np.random.normal(), np.random.random(),
            random.gauss(0, 1), random.random())
def reseed():
    np.random.seed(99)
    random.seed(101)
'''
    tests = [
        # Module initialization also precedes test execution upstream.
        'assert np.random.random() == np.random.RandomState(3).random_sample()\n'
        'assert random.random() == random.Random(5).random()',
        '''np.random.seed(1024)
random.seed(17)
expected_np = np.random.RandomState(1024)
expected_py = random.Random(17)
def expected_draw():
    return (expected_np.randint(0, 10), expected_np.normal(), expected_np.random_sample(),
            expected_py.gauss(0, 1), expected_py.random())
assert draw() == expected_draw()
assert np.random.normal() == expected_np.normal()
assert random.gauss(0, 1) == expected_py.gauss(0, 1)
assert draw() == expected_draw()''',
        # Both generators' cached Gaussian values and positions must survive.
        'assert draw() == expected_draw()\n'
        'assert np.random.random() == expected_np.random_sample()\n'
        'assert random.random() == expected_py.random()',
        'reseed()\n'
        'assert np.random.random() == np.random.RandomState(99).random_sample()\n'
        'assert random.random() == random.Random(101).random()',
    ]
    result = run_signed(tmp_path, code, tests, [None]*len(tests),
                        dependencies='import numpy as np\nimport random')
    assert result['verdicts'] == [True]*len(tests), result


@pytest.mark.parametrize('state', [
    '("MT19937", np.zeros(1, dtype=np.uint32), 0, 0, 0.0)',
    '("MT19937", np.zeros(624, dtype=float), 0, 0, 0.0)',
    '("MT19937", np.zeros(624, dtype=np.uint32), 625, 0, 0.0)',
    '("MT19937", np.zeros(624, dtype=np.uint32), 0, 2, 0.0)',
])
def test_invalid_candidate_random_state_cannot_be_caught_into_pass(tmp_path, state):
    code = ('import numpy as np\ndef candidate():\n'
            '    np.random.get_state = lambda: '+state+'\n    return 7')
    source = 'try:\n    candidate()\nexcept Exception:\n    pass\nassert True'
    result = run_signed(tmp_path, code, [source], [None])
    assert result['verdicts'] == [False] and receipt_failure(result)


def test_rejected_frame_cannot_be_reused_after_budget_reset(monkeypatch):
    from scicode.proxy_protocol import Channel, FailedCall
    channel = Channel(-1, -1, 10, limit=64)
    monkeypatch.setattr(channel, '_read', lambda count: (64).to_bytes(8, 'big'))
    deadline = channel.deadline
    with pytest.raises(FailedCall, match='Reply budget'):
        channel.receive()
    channel.begin_test()
    assert channel.deadline == deadline
    with pytest.raises(FailedCall, match='broken'):
        channel.receive()


def test_partial_reply_deadline_is_signed_incorrect(tmp_path):
    code = 'import os, sys, time\ndef candidate():\n    os.write(int(sys.argv[3]), b"\\x00" * 4)\n    time.sleep(60)'
    result = run_signed(tmp_path, code, ['assert candidate() == target'], [True], timeout=3)
    assert result['verdicts'] == [False] and receipt_failure(result)


def test_upstream_helper_import_path_is_trusted(tmp_path):
    source = 'from scicode.compare.cmp import cmp_tuple_or_list\nassert cmp_tuple_or_list(candidate(), target)'
    code = 'def candidate(): return (1., 2.)\ndef cmp_tuple_or_list(*a): return True'
    result = run_signed(tmp_path, code, [source], [(1., 9.)])
    assert result['verdicts'] == [False]


def test_module_lambda_and_function_alias_are_proxies(tmp_path):
    code = 'constant = 3\nf = lambda x: x + constant\nalias = f'
    result = run_signed(tmp_path, code, ['assert alias(2) == 5 and f(3) == 6'], [None])
    assert result['verdicts'] == [True], result


def test_candidate_initialization_exception_fails_even_without_calls(tmp_path):
    result = run_signed(tmp_path, 'raise ValueError("PRIVATE")', ['assert True'], [None])
    assert result['verdicts'] == [False] and receipt_failure(result)


@pytest.mark.parametrize('code,source', [
    ('def candidate(): return (9.,)\ndef cmp_tuple_or_list(*args): return True',
     'assert cmp_tuple_or_list(candidate(), target)'),
    ('def candidate(): return [0, 0]\ndef all(*args): return True',
     'assert all(v > 10 for v in candidate())'),
    ('def candidate(): return 900\ndef abs(*args): return 0',
     'assert abs(candidate() - 100) < 1'),
])
def test_candidate_cannot_shadow_trusted_predicates(tmp_path, code, source):
    result = run_signed(tmp_path, code, [source], [(1.,)])
    assert result['verdicts'] == [False] and receipt_failure(result)
