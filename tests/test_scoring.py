import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import AsyncMock
import pytest
from inspect_ai.scorer import Target, CORRECT, INCORRECT
from inspect_ai.model import ChatMessageAssistant
from inspect_ai.util import ExecResult
from inspect_ai.util._sandbox.events import SandboxEnvironmentProxy
from scicode.receipts import verify_receipt, receipt_failure, FLAGS
import scicode.scoring as scoring
from scicode.sandbox_runner import SETUP, RUNNER, CLEANUP_COMMAND

KEY=b'a'*32
WORK='/tmp/scicode-fixture'


def signed(**changes):
    record={'returncode':0,'cwd':WORK,'verdicts':[True],**{f:False for f in FLAGS},**changes}
    body=json.dumps(record)
    return json.dumps({'body':body,'tag':hmac.new(KEY,body.encode(),hashlib.sha256).hexdigest()})


@pytest.mark.parametrize('flag',FLAGS)
def test_signed_failures_are_authenticated_incorrect(flag):
    receipt=verify_receipt(signed(**{flag:True}),KEY)
    assert receipt is not None and receipt_failure(receipt)


def test_receipt_forgery_and_invalid_types():
    assert verify_receipt(signed(),b'b'*32) is None
    assert verify_receipt(signed(verdicts=[1]),KEY) is None
    assert verify_receipt(signed(returncode=True),KEY) is None
    assert verify_receipt(signed(verdicts=[False]),KEY) is not None
    assert receipt_failure(verify_receipt(signed(verdicts=[False]),KEY))
    assert receipt_failure(verify_receipt(signed(),KEY)) is None


class FakeSandbox:
    def __init__(self,outcome='pass',cleanup_bad=False):
        self.outcome=outcome;self.cleanup_bad=cleanup_bad;self.calls=[]
    async def exec(self,cmd,input=None,**kwargs):
        self.calls.append(cmd)
        if SETUP in cmd:
            if self.outcome=='setup':
                return ExecResult(False,1,'','SECRET')
            return ExecResult(True,0,json.dumps({'cwd':WORK,'key':KEY.hex()}),'')
        if RUNNER in cmd:
            if self.outcome=='transport':raise ConnectionError('SECRET')
            output = 'SECRET' if self.outcome=='missing' else signed(verdicts=[self.outcome=='pass'])
            return ExecResult(True,0,output,'')
        return ExecResult(not self.cleanup_bad,2 if self.cleanup_bad else 0,'','')


@pytest.mark.parametrize('outcome,kernel,expected',[
    ('pass',None,True),('fail',None,False),('missing',None,None),('transport',None,None),
    ('setup','sandbox memory exhausted during candidate execution',None),
    ('missing','sandbox memory exhausted during candidate execution',False),
    ('transport','sandbox storage exhausted during candidate execution',False),
])
@pytest.mark.parametrize('cleanup_bad',[False,True])
def test_template_missing_receipt_and_cleanup_semantics(monkeypatch,outcome,kernel,expected,cleanup_bad):
    fake=FakeSandbox(outcome,cleanup_bad)
    lookup=AsyncMock(return_value=kernel)
    monkeypatch.setattr(scoring,'sandbox_failure',lookup)
    # Avoid waiting the production outer deadline in a deterministic fake.
    original=scoring.cleanup_candidate
    async def cleanup(env,not_before):await original(env,0)
    monkeypatch.setattr(scoring,'cleanup_candidate',cleanup)
    payload={'timeout':1,'tests':['private']}
    if expected is None:
        with pytest.raises(RuntimeError,match='details withheld') as error:
            asyncio.run(scoring.run_payload(SandboxEnvironmentProxy(fake),payload))
        assert 'SECRET' not in str(error.value)
    else:
        passed,reason,terminal=asyncio.run(scoring.run_payload(SandboxEnvironmentProxy(fake),payload))
        assert passed is (expected and not cleanup_bad)
        if outcome=='fail':assert reason=='test comparison failed'
        if kernel:assert reason==kernel
    if outcome=='setup':lookup.assert_not_called()
    assert any(cmd==CLEANUP_COMMAND for cmd in fake.calls)


@pytest.mark.parametrize('results,value,fraction',[
    ([True,True],CORRECT,1),([True,False],INCORRECT,.5),([False,False],INCORRECT,0)])
def test_main_problem_requires_all_steps(monkeypatch,results,value,fraction):
    record={'problem_id':'fixture','required_dependencies':'','sub_steps':[
        {'step_number':f'1.{i+1}','test_cases':['assert 1 == target']} for i in range(2)]}
    monkeypatch.setattr(scoring,'load_records',lambda *a:[record])
    monkeypatch.setattr(scoring,'sandbox',lambda:object())
    monkeypatch.setattr(scoring,'run_payload',AsyncMock(side_effect=[scoring.StepResult(v,'sanitized') for v in results]))
    state=SimpleNamespace(sample_id='fixture',messages=[ChatMessageAssistant(content='pass')]*2)
    score=asyncio.run(scoring.verify()(state,Target('')))
    assert score.value==value
    assert json.loads(score.explanation)['subproblem_pass_fraction']==fraction
    assert score.answer is None


def _three_steps(monkeypatch):
    record={'problem_id':'fixture','required_dependencies':'','sub_steps':[
        {'step_number':f'1.{i+1}','test_cases':['assert 1 == target']} for i in range(3)]}
    monkeypatch.setattr(scoring,'load_records',lambda *a:[record])
    monkeypatch.setattr(scoring,'sandbox',lambda:object())
    return record


def test_a_trajectory_stopped_by_a_limit_is_scored_as_far_as_it_went(monkeypatch):
    # Prometheus 4.0 hit the token budget before its last reply; the scorer raised and
    # the run could not publish at all (2026-09-25). Answered steps still run their
    # tests; unanswered ones fail as not generated, so the problem is INCORRECT.
    _three_steps(monkeypatch)
    run=AsyncMock(side_effect=[scoring.StepResult(True,'all tests passed'),
                               scoring.StepResult(True,'all tests passed')])
    monkeypatch.setattr(scoring,'run_payload',run)
    state=SimpleNamespace(sample_id='fixture',messages=[ChatMessageAssistant(content='pass')]*2)
    score=asyncio.run(scoring.verify()(state,Target('')))
    explanation=json.loads(score.explanation)
    assert score.value==INCORRECT and run.await_count==2
    assert [s['reason'] for s in explanation['steps']]==['all tests passed','all tests passed','not generated']
    assert explanation['subproblems_passed']==2 and explanation['subproblems_total']==3


def test_no_reply_at_all_scores_every_step_not_generated(monkeypatch):
    _three_steps(monkeypatch)
    run=AsyncMock()
    monkeypatch.setattr(scoring,'run_payload',run)
    state=SimpleNamespace(sample_id='fixture',messages=[])
    score=asyncio.run(scoring.verify()(state,Target('')))
    assert score.value==INCORRECT and run.await_count==0
    assert {s['reason'] for s in json.loads(score.explanation)['steps']}=={'not generated'}


def test_more_replies_than_steps_is_still_refused(monkeypatch):
    # An extra reply (a scaffold appending a critique turn) is not a SciCode trajectory.
    _three_steps(monkeypatch)
    monkeypatch.setattr(scoring,'run_payload',AsyncMock(return_value=scoring.StepResult(True,'ok')))
    state=SimpleNamespace(sample_id='fixture',messages=[ChatMessageAssistant(content='pass')]*4)
    with pytest.raises(RuntimeError, match='details withheld'):
        asyncio.run(scoring.verify()(state,Target('')))


def test_dead_or_unclean_sandbox_is_not_reused(monkeypatch):
    record={'problem_id':'fixture','required_dependencies':'','sub_steps':[
        {'step_number':f'1.{i+1}','test_cases':['assert 1 == target']} for i in range(3)]}
    monkeypatch.setattr(scoring,'load_records',lambda *a:[record])
    monkeypatch.setattr(scoring,'sandbox',lambda:object())
    run=AsyncMock(return_value=scoring.StepResult(False,'memory exhausted',True))
    monkeypatch.setattr(scoring,'run_payload',run)
    state=SimpleNamespace(sample_id='fixture',messages=[ChatMessageAssistant(content='pass')]*3)
    score=asyncio.run(scoring.verify()(state,Target('')))
    assert score.value==INCORRECT and run.await_count==1
    assert json.loads(score.explanation)['subproblems_total']==3


def test_recursive_candidate_inventory_reaches_sandbox_and_scores_incorrect(tmp_path, monkeypatch):
    from proxy_harness import run_signed

    class RealWorkerSandbox(FakeSandbox):
        async def exec(self, cmd, input=None, **kwargs):
            if SETUP in cmd:
                self.payload = json.loads(input)
            if RUNNER in cmd:
                self.calls.append(cmd)
                result = run_signed(tmp_path, self.payload['code'], self.payload['tests'], [None])
                assert result['verdicts'] == [False]
                return ExecResult(True, 0, signed(**result), '')
            return await super().exec(cmd, input=input, **kwargs)

    record = {'problem_id': 'fixture', 'required_dependencies': '', 'sub_steps': [
        {'step_number': 'fixture.1', 'test_cases': ['assert True']}]}
    fake = RealWorkerSandbox()
    monkeypatch.setattr(scoring, 'load_records', lambda *a: [record])
    monkeypatch.setattr(scoring, 'sandbox', lambda: SandboxEnvironmentProxy(fake))
    state = SimpleNamespace(sample_id='fixture', messages=[
        ChatMessageAssistant(content='```python\nx=' + '+'.join(['1'] * 1001) + '\n```')])
    score = asyncio.run(scoring.verify()(state, Target('')))
    assert score.value == INCORRECT
    assert any(SETUP in call for call in fake.calls)
    assert any(RUNNER in call for call in fake.calls)
    assert any(call == CLEANUP_COMMAND for call in fake.calls)


@pytest.mark.parametrize('peak', [0, 123456, -1, True, 1.5, '123', None])
def test_optional_peak_rss_receipt_is_a_nonnegative_integer(peak):
    receipt = verify_receipt(signed(peak_candidate_rss_bytes=peak), KEY)
    assert (receipt is not None) is (type(peak) is int and peak >= 0)
