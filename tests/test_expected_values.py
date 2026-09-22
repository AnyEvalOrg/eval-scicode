"""Reference provenance and inert candidate handles at the real pipe boundary."""
import numpy as np
import pytest
import scipy.sparse

from proxy_harness import run_signed


@pytest.mark.parametrize('assertion', [
    'assert obj == target', 'assert target == obj', 'assert obj',
    'assert bool(obj)', 'assert np.allclose(obj, target)',
    'assert np.array_equal(np.asarray(obj), target)',
    'assert float(obj) == target', 'assert int(obj) == target',
    'assert complex(obj) == target', 'assert obj < target',
    'assert target in obj', 'assert obj + target == target',
    'assert target - obj == target', 'assert len(obj) == target',
    'assert list(obj) == target',
])
def test_handles_cannot_supply_comparison_or_conversion_results(tmp_path, assertion):
    marker = tmp_path / 'operator-called'
    code = f'''class Liar:
    def dishonest(self, *args, **kwargs):
        open({str(marker)!r}, 'w').write('called')
        return True
    __eq__ = __ne__ = __lt__ = __bool__ = __array__ = dishonest
    __float__ = __int__ = __complex__ = __index__ = dishonest
    __contains__ = __add__ = __rsub__ = __len__ = __iter__ = dishonest
def candidate(): return Liar()
'''
    result = run_signed(tmp_path, code, ['obj = candidate()\n' + assertion], [7.])
    assert result['verdicts'] == [False]
    assert not marker.exists()


def test_handle_local_identity_truth_hash_and_display_send_no_requests(tmp_path):
    code = '''class Liar:
    def wrong(self, *args): raise RuntimeError('must not run')
    __eq__ = __ne__ = __bool__ = __hash__ = __str__ = __repr__ = __format__ = wrong
def candidate(): return Liar()
'''
    source = '''obj = candidate()
other = candidate()
assert obj == obj and obj != other and obj != 7 and not (obj == 7)
assert not obj and bool(obj) is False
assert hash(obj) == hash(obj)
assert 'Proxy' in str(obj) and 'Proxy' in repr(obj) and 'Proxy' in format(obj)
'''
    assert run_signed(tmp_path, code, [source], [None])['verdicts'] == [True]


@pytest.mark.parametrize('expression', [
    'target', 'target[0]', 'target[:1]', 'target.copy()', 'target.tolist()',
    'np.asarray(target)', 'np.array(target, copy=True)',
    'np.asarray(target).copy() + 2', 'np.sum(target)', 'np.linalg.norm(target)',
    'np.add(target, 1)', 'np.concatenate([target, target])',
    'np.asarray(target).astype(float).ravel()[0]',
    "{'nested': [target]}", 'float(target[0])', 'int(target[0])',
    'str(target[0])', 'len(target)', 'bool(target[0])',
    'not target[0]', 'target[0] == 7', 'target[0] is None',
    'target[0] and 13', '13 if target[0] else 14',
    '[x + 1 for x in target]',
])
def test_expected_arguments_never_reach_candidate_pipe(tmp_path, expression):
    traffic = tmp_path / 'requests'
    code = f'''import proxy_protocol
_saved_receive = proxy_protocol.Channel.receive
def _record_receive(self):
    request = _saved_receive(self)
    with open({str(traffic)!r}, 'a') as stream: stream.write('request\\n')
    return request
proxy_protocol.Channel.receive = _record_receive
def candidate(value): return 1
def probe(): return 42
'''
    source = f'''value = {expression}
try:
    candidate(value)
except Exception:
    pass
assert True
'''
    result = run_signed(tmp_path, code, [source, 'assert probe() == 42'], [np.array([7., 8.]), None])
    # Even catching the rejected call cannot make the first test pass. The
    # second call proves the worker stayed alive and its receive hook worked.
    assert result['verdicts'] == [False, True]
    assert traffic.read_text().splitlines() == ['request']


@pytest.mark.parametrize('target,expression', [
    (True, 'target'), (7, 'target + 2'), ('secret', 'target.upper()'),
    ({'value': np.array([7.])}, "target['value'] * 2"),
    ((np.array([7.]), 8.), 'target[1]'),
    (scipy.sparse.csr_matrix([[7.]]), 'target.toarray()'),
])
def test_expected_types_are_rejected_before_serialization(target, expression):
    from scicode.expected_values import References, compile_test
    from scicode.proxy_protocol import Client, FailedCall, Proxy

    class NoTraffic:
        def send(self, value):
            pytest.fail('Expected data reached channel.send')

    client = Client(NoTraffic())
    refs = client.expected = References(client)
    refs.remember(target)
    namespace = {'np': np, '_scicode_references': refs, 'target': refs.wrap(target),
                 'candidate': Proxy(client, ('binding', 'candidate'))}
    with pytest.raises(FailedCall):
        exec(compile_test(f'candidate({expression})'), namespace)
    assert client.failed


@pytest.mark.parametrize('source', [
    'out = np.zeros(2)\nnp.add(target, 1, out=out)\ncandidate(out)',
    'out = np.zeros(2)\nnp.copyto(out, target)\ncandidate(out.copy() + 1)',
    'alias = target\ncandidate(value=alias)',
    'def helper(value):\n    return candidate(value)\nhelper(target)',
    'def helper(value):\n    return np.asarray(value) + 1\ncandidate(helper(target))',
    'items = []\nitems.append(target)\ncandidate(items)',
    'for value in map(candidate, target):\n    pass',
    'out = np.zeros(2)\ncopy = out.copy\nnp.copyto(out, target)\ncandidate(copy())',
    'out = np.zeros(4)\nnp.copyto(out[:2], target)\ncandidate(out)',
])
def test_expected_aliases_outputs_and_callbacks_fail_closed(tmp_path, source):
    marker = tmp_path / 'received'
    code = f'def candidate(*args, **kwargs):\n    open({str(marker)!r}, "w").write("received")'
    result = run_signed(tmp_path, code, [source], [np.array([7., 8.])])
    assert result['verdicts'] == [False]
    assert not marker.exists()


def test_expected_provenance_persists_between_test_cases(tmp_path):
    marker = tmp_path / 'received'
    code = f'def candidate(value):\n    open({str(marker)!r}, "w").write("received")'
    sources = ['saved = np.asarray(target) + 1', 'candidate(saved)']
    assert run_signed(tmp_path, code, sources, [np.array([7.]), None])['verdicts'] == [True, False]
    assert not marker.exists()


def test_only_explicit_attribute_reads_are_forwarded(tmp_path):
    marker = tmp_path / 'implicit-probe'
    code = f'''class Object:
    value = 7
    @property
    def dtype(self):
        open({str(marker)!r}, 'w').write('probed')
        return 'float64'
def candidate(): return Object()
'''
    # This helper represents library code, not an explicit read in the test.
    dependencies = 'def library_probe(obj): return getattr(obj, "dtype", None)'
    source = '''obj = candidate()
assert library_probe(obj) is None
assert obj.value == 7 and getattr(obj, 'value') == 7
assert hasattr(obj, 'value') and not hasattr(obj, 'missing')
assert getattr(obj, 'missing', 9) == 9
'''
    assert run_signed(tmp_path, code, [source], [None], dependencies=dependencies)['verdicts'] == [True]
    assert not marker.exists()


def test_scalar_inserted_into_mutable_container_keeps_provenance(tmp_path):
    marker = tmp_path / 'received'
    code = f'def candidate(value):\n    open({str(marker)!r}, "w").write("received")'
    source = 'items = []\nitems.append(float(target))\ncandidate(items)'
    assert run_signed(tmp_path, code, [source], [7.])['verdicts'] == [False]
    assert not marker.exists()


def test_instrumentation_preserves_arrays_and_short_circuit_evaluation():
    from scicode.expected_values import References, compile_test
    from scicode.proxy_protocol import Client
    refs = References(Client(None))
    namespace = {'np': np, '_scicode_references': refs}
    exec(compile_test('''
calls = []
def operand(value):
    calls.append(value)
    return value
assert operand(1) < operand(2) < operand(3)
assert calls == [1, 2, 3]
assert not (operand(2) < operand(1) < operand(0))
assert calls == [1, 2, 3, 2, 1]
x = np.array([1, 2])
assert np.all(x == x)
assert np.all(True and x == x)
assert np.all(False or x == x)
assert not (False and operand(0))
assert True or operand(0)
assert calls == [1, 2, 3, 2, 1]
'''), namespace)
