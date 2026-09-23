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


@pytest.mark.parametrize('peak_rss',[True,False])
@pytest.mark.parametrize('step_id',['1.1','78.3','70.8'])
@pytest.mark.parametrize('passed',[True,False])
def test_canonical_calls_real_supervisor_and_authenticates(monkeypatch,passed,step_id,peak_rss):
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        if SETUP in command:return SimpleNamespace(returncode=0,stdout=json.dumps({'cwd':WORK,'key':KEY.hex()}))
        if RUNNER in command:return SimpleNamespace(returncode=0,stdout=signed(verdicts=[passed], **({'peak_candidate_rss_bytes':123456} if peak_rss else {})))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(canonical.subprocess,'run',run)
    result=canonical.check_step('authored fixture',{'step_number':step_id,'test_cases':['assert 1 == target']},3,peak_rss=peak_rss)
    assert result['passed'] is passed
    assert result['status'] == ('passed' if passed else 'failed' if step_id == '1.1' else 'known_upstream_defect')
    assert ('peak_candidate_rss_bytes' in result) is peak_rss
    assert ('measure_peak_rss' in json.loads(calls[0][1]['input'])) is peak_rss
    if peak_rss:
        assert result['peak_candidate_rss_bytes'] == 123456
    assert any(RUNNER in cmd for cmd,_ in calls)
    assert calls[-2][0]==CLEANUP_COMMAND and calls[-1][0]==QUIESCENCE_COMMAND
    assert 'ground_truth_code' not in calls[0][1]['input']


def test_cloudbuild_runs_reference_check_with_supervisor_capabilities():
    config=yaml.safe_load(Path('scripts/cloudbuild-canonical.yaml').read_text())
    command=config['steps'][0]['args'][-1]
    for text in ['--cap-add SYS_PTRACE','--cap-add SETUID','--network none','--read-only','--memory 6g','--peak-rss','scripts/canonical_check.py']:
        assert text in command
    assert config['substitutions']['_SANDBOX_IMAGE'].endswith('eval-scicode-sandbox:1.0.0')


@pytest.mark.parametrize('reason_flag', ['memory_exceeded', 'timeout', 'cleanup_failed', 'supervisor_error'])
def test_known_defect_does_not_mask_operational_failure(monkeypatch, reason_flag):
    def run(command, **kwargs):
        if SETUP in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps({'cwd':WORK,'key':KEY.hex()}))
        if RUNNER in command:
            return SimpleNamespace(returncode=0, stdout=signed(verdicts=[False], **{reason_flag:True}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(canonical.subprocess, 'run', run)
    result = canonical.check_step('fixture', {'step_number':'78.3','test_cases':['assert True']}, 3)
    assert result['status'] == 'failed'
    assert canonical.summarize_results([result])['unexpected_failures'] == 1


@pytest.mark.parametrize('unexpected', [False, True])
@pytest.mark.parametrize('peak_rss', [False, True])
def test_main_counts_all_steps_and_exits_only_for_unexpected_failures(monkeypatch, tmp_path, unexpected, peak_rss):
    output = tmp_path/'report.json'
    monkeypatch.setattr(canonical.sys, 'platform', 'linux')
    monkeypatch.setattr(canonical.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(canonical.sys, 'argv', ['canonical_check', '--output', str(output)] + (['--peak-rss'] if peak_rss else []))
    def check(code, step, timeout, dependencies, measure):
        step_id = step['step_number']
        status = ('known_upstream_defect' if step_id in canonical.KNOWN_UPSTREAM_DEFECTS
                  else 'failed' if unexpected and step_id == '10.11' else 'passed')
        result = {'step':step_id, 'passed':status == 'passed', 'status':status,
                  'reason':'all tests passed' if status == 'passed' else 'test comparison failed'}
        if measure:
            result['peak_candidate_rss_bytes'] = 999 if step_id == '10.11' else 123
        return result
    monkeypatch.setattr(canonical, 'check_step', check)
    assert canonical.main() == int(unexpected)
    report = json.loads(output.read_text())
    assert report['total'] == len(report['results']) == 50
    assert report['passed'] == 48 - int(unexpected)
    assert report['known_upstream_defects'] == 2
    assert report['unexpected_failures'] == int(unexpected)
    assert ('peak_candidate_rss_bytes' in report) is peak_rss
    if peak_rss:
        assert report['peak_candidate_rss_bytes'] == 999
