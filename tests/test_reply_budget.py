"""Reply budget sized from measured correct replies in both populations.

Measured (scripts/reply_replays.py through the real two-process executor, answers
widened to 64-bit dtypes): the largest single reply frame and the largest per-test reply
total are both 63.2 case 2: 170,683,855 and 170,786,610 bytes (162.8 / 162.9 MiB). Every
stored target outside the replayed steps encodes (widened) to at most 32,000,570 bytes
(13.1), so the replayed steps bound both populations.
"""
import os
import pickle
import runpy
import struct
from pathlib import Path

import pytest

from scicode import comparison_worker, execution, proxy_protocol, safe_serialization
from scicode.dataset import load_records, load_verified_records
from scicode.execution import execution_request
from scicode.receipts import receipt_failure

REPLAYS = runpy.run_path('scripts/reply_replays.py')
MIB = 1024 * 1024
LARGEST_MEASURED_FRAME = 170_683_855
LARGEST_MEASURED_TEST_TOTAL = 170_786_610
POPULATIONS = {'scicode': (Path('scicode/test_data.h5'), load_records),
               'verified': (Path('scicode/test_data_cleaned.h5'), load_verified_records)}


def test_budget_constants_leave_headroom_over_the_measurement():
    assert safe_serialization.MAX_BYTES == 256 * MIB
    assert execution.REPLY_LIMIT == 256 * MIB
    assert execution.OUTPUT_LIMIT == 32 * MIB  # candidate stdout/stderr/file size: unchanged
    assert LARGEST_MEASURED_FRAME * 1.5 < safe_serialization.MAX_BYTES
    assert LARGEST_MEASURED_TEST_TOTAL * 1.5 < execution.REPLY_LIMIT
    # Executor cap: see README.md (measured VmPeak) and the pod budget in values.yaml.
    assert comparison_worker.ADDRESS_SPACE == 1280 * MIB
    assert 4096 * MIB + comparison_worker.ADDRESS_SPACE + 256 * MIB + 512 * MIB <= 6 * 1024 * MIB


def test_requests_carry_the_reply_limit_separately_from_output():
    step = load_records()[0]['sub_steps'][0]
    request = execution_request('x', step)
    assert request['reply_limit'] == execution.REPLY_LIMIT
    assert request['output_limit'] == execution.OUTPUT_LIMIT


def header_channel(size, limit=None):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, struct.pack('!Q', size))
    channel = proxy_protocol.Channel(read_fd, os.open(os.devnull, os.O_WRONLY), 5,
                                     execution.REPLY_LIMIT if limit is None else limit)
    return channel, write_fd


@pytest.mark.parametrize('size', [safe_serialization.MAX_BYTES, execution.REPLY_LIMIT, 2**63])
def test_an_over_budget_frame_is_refused_before_allocation(size):
    channel, _ = header_channel(size)
    with pytest.raises(proxy_protocol.FailedCall, match='Reply budget exceeded'):
        channel.receive()
    assert channel.broken
    with pytest.raises(proxy_protocol.FailedCall):
        channel.receive()


def test_the_per_test_budget_is_aggregate_across_frames():
    data = safe_serialization.dumps(list(range(1000)))
    read_fd, write_fd = os.pipe()
    channel = proxy_protocol.Channel(read_fd, os.open(os.devnull, os.O_WRONLY), 5, 2 * len(data) + 1)
    for _ in range(2):
        os.write(write_fd, struct.pack('!Q', len(data)) + data)
        assert channel.receive() == list(range(1000))
    os.write(write_fd, struct.pack('!Q', len(data)))
    with pytest.raises(proxy_protocol.FailedCall, match='Reply budget exceeded'):
        channel.receive()
    channel.broken = False
    channel.begin_test()
    assert channel.remaining == 2 * len(data) + 1


def test_a_frame_above_the_largest_measurement_still_decodes_within_the_budget():
    import numpy as np
    value = np.zeros(LARGEST_MEASURED_FRAME // 8 // 4 * 3, dtype=np.float64)  # ~1.0x the measured frame
    data = safe_serialization.dumps(value)
    assert LARGEST_MEASURED_FRAME * 0.9 < len(data) < safe_serialization.MAX_BYTES
    assert safe_serialization.loads(data).shape == value.shape


def test_candidate_results_at_the_frame_limit_are_signed_incorrect(tmp_path):
    from proxy_harness import run_signed
    # nbytes == MAX_BYTES: the candidate cannot encode it; the call fails and is signed.
    code = f'import numpy as np\ndef big():\n    return np.zeros({safe_serialization.MAX_BYTES // 8})'
    result = run_signed(tmp_path, code, ['assert big() is not None'], [None], timeout=120)
    assert result['verdicts'] == [False] and receipt_failure(result) == 'test comparison failed'


def test_small_reply_limits_still_bound_replies_end_to_end(tmp_path):
    from proxy_harness import run_signed
    code = 'def candidate(): return "x" * 40000'
    result = run_signed(tmp_path, code, ['assert len(candidate()) == 40000'] * 2, [None] * 2,
                        reply_limit=65536)
    assert result['verdicts'] == [True, True]
    (tmp_path / 'aggregate').mkdir()
    result = run_signed(tmp_path / 'aggregate', code,
                        ['for i in range(5):\n    assert len(candidate()) == 40000'], [None], reply_limit=65536)
    assert result['verdicts'] == [False] and receipt_failure(result)


def replay_cases():
    return [(population, step) for population in POPULATIONS for step in REPLAYS['STEPS']]


@pytest.mark.parametrize('population,step_id', replay_cases(), ids=lambda v: str(v))
def test_largest_correct_replies_pass_through_the_real_executor(tmp_path, population, step_id):
    """Formerly impossible cases: 13.14/3, 53.4/3, 63.2/2, 63.4/2, 63.5/1-3 (both tasks)."""
    h5, loader = POPULATIONS[population]
    if not h5.exists():
        pytest.skip('HDF5 targets not available locally')
    from proxy_harness import run_signed
    from scicode import process_data
    record = next(r for r in loader() if r['problem_id'] == step_id.split('.')[0])
    step = next(s for s in record['sub_steps'] if s['step_number'] == step_id)
    process_data.H5PY_FILE = str(h5)
    targets = process_data.process_hdf5_to_tuple(step_id, len(step['test_cases']))
    replay = tmp_path / 'replay.pickle'
    replay.write_bytes(pickle.dumps(targets))
    work = tmp_path / 'work'
    work.mkdir()
    code = REPLAYS['candidate_code'](record['required_dependencies'], step_id, replay)
    result = run_signed(work, code, step['test_cases'], targets,
                        dependencies=record['required_dependencies'], timeout=300)
    failed = [i for i, passed in enumerate(result['verdicts'], 1) if not passed]
    assert receipt_failure(result) is None, f'{population} {step_id}: failed cases {failed}'


@pytest.mark.parametrize('verdicts', [[True, False], []])
def test_measured_runs_report_executor_peak_without_changing_verdicts(tmp_path, verdicts):
    from proxy_harness import run_signed
    tests = ['assert candidate() == 1' if v else 'assert candidate() == 2' for v in verdicts]
    result = run_signed(tmp_path, 'def candidate(): return 1', tests, [None] * len(tests), measure=True)
    assert result['verdicts'] == verdicts
    assert type(result['peak_executor_vm_bytes']) is int and result['peak_executor_vm_bytes'] >= 0
    (tmp_path / 'plain').mkdir()
    unmeasured = run_signed(tmp_path / 'plain', 'def candidate(): return 1', tests, [None] * len(tests))
    assert unmeasured['verdicts'] == verdicts and 'peak_executor_vm_bytes' not in unmeasured


def test_receipt_rejects_invalid_executor_peaks():
    from test_scoring import KEY, signed
    from scicode.receipts import verify_receipt
    assert verify_receipt(signed(peak_executor_vm_bytes=5), KEY)['peak_executor_vm_bytes'] == 5
    for bad in (-1, 1.5, '5', True):
        assert verify_receipt(signed(peak_executor_vm_bytes=bad), KEY) is None
