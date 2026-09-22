import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
from scicode.dataset import load_records
from scicode.sandbox_runner import SETUP,RUNNER,CLEANUP_COMMAND,QUIESCENCE_COMMAND
from test_scoring import signed,KEY,WORK

spec=importlib.util.spec_from_file_location('canonical_check',Path('scripts/canonical_check.py'))
canonical=importlib.util.module_from_spec(spec);spec.loader.exec_module(canonical)


def test_all_dev_references_present_test_references_absent():
    dev=load_records(dev_only=True)
    assert sum(len(r['sub_steps']) for r in dev)==50
    assert all(s['ground_truth_code'] for r in dev for s in r['sub_steps'])
    assert all('ground_truth_code' not in s for r in load_records() for s in r['sub_steps'])


@pytest.mark.parametrize('passed',[True,False])
def test_canonical_calls_real_supervisor_and_authenticates(monkeypatch,passed):
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        if SETUP in command:return SimpleNamespace(returncode=0,stdout=json.dumps({'cwd':WORK,'key':KEY.hex()}))
        if RUNNER in command:return SimpleNamespace(returncode=0,stdout=signed(verdicts=[passed]))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(canonical.subprocess,'run',run)
    result=canonical.check_step('authored fixture',{'step_number':'1.1','test_cases':['assert 1 == target']},3)
    assert result['passed'] is passed
    assert any(RUNNER in cmd for cmd,_ in calls)
    assert calls[-2][0]==CLEANUP_COMMAND and calls[-1][0]==QUIESCENCE_COMMAND
    assert 'ground_truth_code' not in calls[0][1]['input']


def test_cloudbuild_runs_reference_check_with_supervisor_capabilities():
    config=yaml.safe_load(Path('scripts/cloudbuild-canonical.yaml').read_text())
    command=config['steps'][0]['args'][-1]
    for text in ['--cap-add SYS_PTRACE','--cap-add SETUID','--network none','--read-only','scripts/canonical_check.py']:
        assert text in command
    assert config['substitutions']['_SANDBOX_IMAGE'].endswith('eval-scicode-sandbox:1.0.0')
