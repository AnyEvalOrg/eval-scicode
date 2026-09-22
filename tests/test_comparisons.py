import ast
import base64
import io
import json
import numpy as np
import scipy.sparse as sparse
import sympy
import pytest
from scicode import test_util as current
from scicode.safe_serialization import dumps, loads
from scicode.test_plan import split_test, compare_plan
from test_dataset_protocol import upstream

original = upstream('comparison')


def outcome(fn,*args,**kwargs):
    try:
        return ('result',bool(fn(*args,**kwargs)))
    except Exception as exc:
        return ('error',type(exc).__name__)


@pytest.mark.parametrize('left,right',[
    ([1.0],[1.000001]), ([1.0],[1.01]), ([0.0],[1e-8]), ([0.0],[1.01e-8]),
    ([float('nan')],[float('nan')]), ([True],[1]), ([False],[True]),
    ([1,2],[1]), ([np.array([1,2])],[np.array([1,2])]),
    ([{'a':1.0}],[{'a':1.000001}]), ([{'a':'s'}],[{'a':'t'}]),
    ([sparse.csr_matrix([[1,0]])],[sparse.csc_matrix([[1,0]])]),
    ([sparse.csc_matrix([[1,0]])],[sparse.csr_matrix([[1,0.1]])]),
])
def test_tuple_upstream_and_wire_semantics(left,right):
    expected = outcome(original.cmp_tuple_or_list,left,right)
    assert outcome(current.cmp_tuple_or_list,left,right) == expected
    assert outcome(current.cmp_tuple_or_list,loads(dumps(left)),right) == expected


@pytest.mark.parametrize('left,right',[
    ({'a':1},{'b':1}), ({'a':np.array([0.0])},{'a':np.array([1e-8])}),
    ({sympy.Symbol('x'):sympy.Symbol('y')},{'x':'y'}),
    ({'a':sparse.bsr_matrix([[1,2]])},{'a':sparse.bsr_matrix([[1,2]])}),
    ({'a':sparse.coo_matrix([[1,2]])},{'a':sparse.coo_matrix([[1,3]])}),
    ({'a':[1,2]},{'a':[1]}),
])
@pytest.mark.parametrize('kwargs',[{}, {'atol':1e-3,'rtol':0}])
def test_dict_upstream_semantics(left,right,kwargs):
    assert outcome(current.are_dicts_close,left,right,**kwargs) == outcome(original.are_dicts_close,left,right,**kwargs)


@pytest.mark.parametrize('a,b',[([[1,0]],[[1,0]]),([[1,0]],[[1,1e-7]])])
def test_csc_upstream_semantics(a,b):
    a,b=sparse.csc_matrix(a),sparse.csr_matrix(b)
    assert outcome(current.are_csc_matrix_close,a,b) == outcome(original.are_csc_matrix_close,a,b)


@pytest.mark.parametrize('value',[None,True,42,1.25,1+2j,'text',(1,[2]),{'x':np.array([1.,2.])},
                                  np.bool_(True),np.int64(3),sympy.Symbol('x'),sparse.csc_matrix([[1,0]])])
def test_wire_is_data_only(value):
    decoded=loads(dumps(value))
    assert type(decoded) is type(value)


def test_reject_pickle_array_and_oversized_declared_shape():
    for array in [np.array([object()],dtype=object),np.array([1])]:
        stream=io.BytesIO();np.save(stream,array,allow_pickle=True)
        raw=stream.getvalue()
        if array.dtype != object:
            raw=raw.replace(b'(1,)',b'(999999999999,)')
        with pytest.raises(Exception):
            loads(json.dumps(['array',base64.b64encode(raw).decode()]))
    with pytest.raises(ValueError):
        loads(b'["pickle", "malicious"]')


@pytest.mark.parametrize('source,target,passed',[
    ('x = np.array([1.,2.])\nassert np.allclose(x,target,rtol=0,atol=1e-6)',np.array([1.,2.0000005]),True),
    ('x = np.array([1.,2.])\nassert np.allclose(x,target,rtol=0,atol=1e-6)',np.array([1.,2.000002]),False),
    ('x = np.array([1.,2.])\na,b=target\nassert np.allclose(x,a) and b == True',(np.array([1.,2.]),True),True),
    ('x = (True, False)\nassert x == target',(True,False),True),
    ('x = np.array([True])\nassert x.any() == target.any()',np.array([False]),False),
])
def test_split_matches_upstream_assertions(source,target,passed):
    env={'np':np,'target':target}
    try:
        exec(source,env); expected=True
    except AssertionError: expected=False
    plan=split_test(source); child={'np':np};exec(plan['compute'],child)
    values=[eval(v,child) for v in plan['operands']]
    assert compare_plan(plan,loads(dumps(values)),target) == expected == passed


def test_root_comparator_is_not_candidate_monkeypatch():
    plan=split_test('x = candidate()\nassert np.allclose(x, target)')
    assert compare_plan(plan,[np.array([9.])],np.array([1.])) is False


def test_dev_literal_reference_recomputed_by_supervisor():
    plan=split_test('expected = np.array([1.0])\nactual = candidate()\nassert np.allclose(actual, expected)')
    assert plan['operands'] == ['actual']
    assert not compare_plan(plan,[np.array([9.])],None)


@pytest.mark.parametrize('value',[sympy.Integer(3),sympy.Rational(1,3),sympy.Float('1.234567890123')])
def test_symbolic_numbers_preserve_upstream_comparison_behavior(value):
    decoded=loads(dumps(value))
    assert type(decoded) is type(value) and decoded == value
    assert outcome(original.cmp_tuple_or_list,[decoded],[1.0]) == outcome(original.cmp_tuple_or_list,[value],[1.0])


def test_object_arrays_are_never_coerced_to_passing_numeric_results():
    with pytest.raises(ValueError,match='Object arrays'):
        dumps(np.array([sympy.Integer(1)],dtype=object))


@pytest.mark.parametrize('source,bad,good', [
    ('assert (np.abs(np.mean(actual - 100) / 100) < .1) == target', [np.array([900.])], [np.array([105.])]),
    ('assert (np.mean(actual) == 0) == target', [np.array([1.])], [np.array([0.])]),
    ('assert ((np.abs(actual) < .1).all(), np.isclose(other, 2)) == target',
     [np.array([9.]), 2.], [np.array([.01]), 2.]),
])
def test_nested_predicates_compare_raw_numerical_outputs(source, bad, good):
    plan = split_test(source)
    target = (True, True) if 'other' in source else True
    assert not compare_plan(plan, bad, target)
    assert compare_plan(plan, good, target)
    # A child monkeypatch which returns the target boolean for the predicate
    # cannot stand in for an incorrect raw result (the first two old plans did).
    if 'other' not in source:
        assert plan['operands'] == ['actual']
        assert not compare_plan(plan, [True], target)


@pytest.mark.parametrize('step_id', ['77.12', '60.5', '64.6', '35.3', '80.7'])
def test_packaged_nested_predicates_cannot_be_replaced_with_expected_boolean(step_id):
    from scicode.dataset import load_records
    step = next(s for r in load_records(True) for s in r['sub_steps'] if s['step_number'] == step_id)
    for source in step['test_cases']:
        plan = split_test(source)
        assert not compare_plan(plan, [True], True)
        assert all(not any(isinstance(n, (ast.Compare, ast.BoolOp))
                           for n in ast.walk(ast.parse(op)))
                   for op in plan['operands'])
        assert any(name in plan['operands'] for name in ('T_sim', 'mu_ext_list', 'Num_particle_Trace', 'A', 'instant_T_array'))


def test_named_predicates_use_underlying_results():
    plan = split_test('actual = candidate()\npassed = np.isclose(actual, 100)\nassert passed == target')
    assert plan['operands'] == ['actual']
    assert not compare_plan(plan, [True], True)
    assert not compare_plan(plan, [900.], True)
    assert compare_plan(plan, [100.], True)


def test_generator_predicate_stays_in_trusted_comparison():
    plan = split_test('actual = candidate()\nassert all(x > 10 for x in actual) == target')
    assert plan['operands'] == ['actual']
    assert compare_plan(plan, [[11, 12]], True)
    assert not compare_plan(plan, [[1, 2]], True)
