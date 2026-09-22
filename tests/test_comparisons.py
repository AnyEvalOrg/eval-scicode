import base64
import io
import json
import numpy as np
import scipy.sparse as sparse
import sympy
import pytest
from scicode import test_util as current
from scicode.safe_serialization import dumps, loads
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


@pytest.mark.parametrize('value',[sympy.Integer(3),sympy.Rational(1,3),sympy.Float('1.234567890123')])
def test_symbolic_numbers_preserve_upstream_comparison_behavior(value):
    decoded=loads(dumps(value))
    assert type(decoded) is type(value) and decoded == value
    assert outcome(original.cmp_tuple_or_list,[decoded],[1.0]) == outcome(original.cmp_tuple_or_list,[value],[1.0])


def test_object_arrays_are_never_coerced_to_passing_numeric_results():
    with pytest.raises(ValueError,match='Object arrays'):
        dumps(np.array([sympy.Integer(1)],dtype=object))




@pytest.mark.parametrize('fmt', ['csr', 'csc', 'coo', 'bsr'])
def test_sparse_wire_preserves_components_without_densifying(fmt, monkeypatch):
    value = getattr(sparse, fmt + '_matrix')(np.array([[1., 0., 3.], [0., 4., 0.]]))
    expected = value.toarray()
    monkeypatch.setattr(type(value), 'toarray', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No densification')))
    decoded = loads(dumps(value))
    assert type(decoded) is type(value)
    assert np.array_equal(decoded.tocoo().data, value.tocoo().data)
    assert decoded.shape == expected.shape


@pytest.mark.parametrize('indices,indptr', [([9], [0, 1]), ([0], [0, 9]), ([0], [1, 1]), ([-1], [0, 1])])
def test_sparse_wire_rejects_invalid_indices(indices, indptr):
    from scicode.safe_serialization import encode
    raw = ['sparse', 'csr', [1, 2], [encode(np.array([1.])), encode(np.array(indices)), encode(np.array(indptr))]]
    with pytest.raises(ValueError):
        loads(json.dumps(raw).encode())
