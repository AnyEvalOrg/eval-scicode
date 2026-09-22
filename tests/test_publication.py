import asyncio
import contextvars
import json
import logging
from types import SimpleNamespace
import pytest
from inspect_ai.log._transcript import Transcript, _transcript
from inspect_ai.model import ChatMessageAssistant
from inspect_ai.scorer import Target
from inspect_ai.util._sandbox.events import SandboxEnvironmentProxy
from scicode.publication import private_grading
import scicode.scoring as scoring
from test_scoring import FakeSandbox


@pytest.mark.parametrize('outcome',['pass','fail','setup'])
def test_private_records_requests_and_tracebacks_are_not_published(monkeypatch,caplog,outcome):
    secret='PRIVATE_TEST_SENTINEL'
    record={'problem_id':'fixture','required_dependencies':'','sub_steps':[{'step_number':'1.1','test_cases':[f'x={secret!r}\nassert 1 == target']}], 'ground_truth_code':'PRIVATE_ANSWER_SENTINEL'}
    monkeypatch.setattr(scoring,'load_records',lambda *a:[record])
    class LoggingSandbox(FakeSandbox):
        async def exec(self,*a,**kw):
            logging.getLogger('k8s_sandbox._logger').error(secret)
            return await super().exec(*a,**kw)
    fake=LoggingSandbox(outcome)
    monkeypatch.setattr(scoring,'sandbox',lambda:SandboxEnvironmentProxy(fake))
    transcript=Transcript();token=_transcript.set(transcript)
    try:
        state=SimpleNamespace(sample_id='fixture',messages=[ChatMessageAssistant(content='pass')])
        try:
            result=asyncio.run(scoring.verify()(state,Target(''))).model_dump(mode='json')
        except RuntimeError as exc:
            from inspect_ai._util.rich import format_traceback
            monkeypatch.setenv('INSPECT_TRACEBACK_LOCALS','1')
            result=format_traceback(type(exc),exc,exc.__traceback__.tb_next)
        export=json.dumps({'result':result,'events':[e.model_dump(mode='json') for e in transcript.events]})
        assert secret not in export and 'PRIVATE_ANSWER_SENTINEL' not in export
        assert secret not in caplog.text
        assert not any(e.event=='sandbox' for e in transcript.events)
    finally:_transcript.reset(token)


def test_private_logging_is_context_scoped(caplog):
    proxy=SandboxEnvironmentProxy(FakeSandbox())
    with private_grading(proxy):
        assert proxy._events
        logging.getLogger('k8s_sandbox._logger').error('PRIVATE')
        contextvars.Context().run(logging.getLogger('k8s_sandbox._logger').error,'PUBLIC')
    assert 'PRIVATE' not in caplog.text and 'PUBLIC' in caplog.text
    with pytest.raises(TypeError):
        with private_grading(object()):pass


@pytest.mark.parametrize('background', [False, True])
def test_generation_traceback_locals_contain_only_public_prompts(monkeypatch, background):
    import copy
    import importlib
    from inspect_ai._util.rich import format_traceback
    solver_module = importlib.import_module('scicode.solver')
    record = copy.deepcopy(solver_module.load_records(True)[0])
    record['general_solution'] = 'PRIVATE_REFERENCE_SENTINEL'
    for step in record['sub_steps']:
        step['ground_truth_code'] = 'PRIVATE_REFERENCE_SENTINEL'
        step['test_cases'] = ['PRIVATE_TEST_SENTINEL']
    monkeypatch.setattr(solver_module, 'load_records', lambda *args: [record])
    monkeypatch.setenv('INSPECT_TRACEBACK_LOCALS', '1')
    solve = solver_module.solve_scicode_problem(background)
    state = SimpleNamespace(sample_id=record['problem_id'], messages=[])

    async def generate(state):
        raise RuntimeError('generation failed')

    async def capture():
        try:
            await solve(state, generate)
        except RuntimeError as error:
            # Begin at the solver frame, as Inspect's error logger would.
            tb = error.__traceback__.tb_next
            rendered = str(format_traceback(type(error), error, tb))
            while tb:
                # Inspect pretty-printers may truncate long locals. Check the
                # complete frame locals too so private records cannot hide there.
                rendered += repr(tb.tb_frame.f_locals)
                tb = tb.tb_next
            return rendered
    exported = asyncio.run(capture())
    assert 'PRIVATE_REFERENCE_SENTINEL' not in exported
    assert 'PRIVATE_TEST_SENTINEL' not in exported
    assert 'PRIVATE_REFERENCE_SENTINEL' not in repr([c.cell_contents for c in solve.__closure__])
