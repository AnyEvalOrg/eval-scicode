import ast
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser
from scicode.dataset import load_records, get_dataset, record_to_sample, manifest
from scicode.solver import solve_scicode_problem, composed_code
from scicode import prompt_templates as prompts
from scicode.execution import execution_request


def upstream(name):
    spec = importlib.util.spec_from_file_location('upstream_'+name, Path(__file__).parent/'upstream'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_counts_ids_integrity_and_sample_hygiene():
    assert len(get_dataset()) == 65
    assert len(get_dataset(True)) == 80
    assert len(load_records(dev_only=True)) == 15
    for record, sample in zip(load_records(True), get_dataset(True)):
        assert sample.id == record['problem_id']
        assert sample.input == record['problem_description_main']
        assert sample.metadata in ({}, None)
        assert sample.target == ''
        assert not sample.files and not sample.setup
        forbidden = ['ground_truth_code', 'test_cases', 'general_solution', 'general_tests']
        exported = json.dumps(sample.model_dump(mode='json'))
        assert all(k not in exported for k in forbidden)
    assert [r['problem_id'] for r in load_records()] == manifest()['splits']['all']['task_ids']


@pytest.mark.parametrize('background',[False,True])
def test_prompts_equal_upstream_bytes_for_every_record(background):
    original = upstream('prompt_templates')
    initial = 'INITIAL_PROMPT_PROVIDE_BACKGROUND' if background else 'INITIAL_PROMPT'
    sub = 'SUBPROBLEM_PROMPT_PROVIDE_BACKGROUND' if background else 'SUBPROBLEM_PROMPT'
    assert getattr(prompts,initial).encode() == getattr(original,initial).encode()
    assert getattr(prompts,sub).encode() == getattr(original,sub).encode()
    for record in load_records(True):
        assert getattr(prompts,initial).format(**record).encode() == getattr(original,initial).format(**record).encode()
        for step in record['sub_steps']:
            assert getattr(prompts,sub).format(**step).encode() == getattr(original,sub).format(**step).encode()


@pytest.mark.parametrize('background',[False,True])
def test_sequential_messages_and_previous_code(background):
    record = load_records()[0]
    state = SimpleNamespace(sample_id=record['problem_id'], messages=[ChatMessageUser(content=record['problem_description_main'])])
    generated = []
    async def generate(state):
        index = len(generated)
        assert len([m for m in state.messages if m.role == 'assistant']) == index
        generated.append(state.messages[-1].text)
        state.messages.append(ChatMessageAssistant(content=f'```python\n# step {index+1}\n```'))
        return state
    asyncio.run(solve_scicode_problem(background)(state,generate))
    assert len(generated) == len(record['sub_steps'])
    for i,step in enumerate(record['sub_steps']):
        code = composed_code(record,state.messages,step)
        assert all(f'# step {j+1}' in code for j in range(i+1))
        assert f'# step {i+2}' not in code
    assert not hasattr(state,'store')


@pytest.mark.parametrize('record',load_records(True),ids=lambda r:r['problem_id'])
def test_every_upstream_test_is_transmitted_unchanged(record):
    for step in record['sub_steps']:
        request = execution_request('def candidate(): return 0', step,
                                    dependencies=record['required_dependencies'])
        assert request['tests'] is step['test_cases']
        assert request['dependencies'] == record['required_dependencies']
        assert 'bindings' not in request  # Candidate source is opaque on the host.


def test_supplied_inspect_keeps_original_known_bad_steps():
    steps = {s['step_number'] for r in load_records(True) for s in r['sub_steps']}
    assert {'13.6','62.1','76.3'} <= steps
