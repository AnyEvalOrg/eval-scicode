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
    assert result['status'] == ('passed' if passed else 'failed')
    assert result['failed_cases'] == ([] if passed else [1])
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
    for text in ['--cap-add SYS_PTRACE','--cap-add SETUID','--network none','--read-only','--memory 6g','--peak-rss','--replays','scripts/canonical_check.py']:
        assert text in command
    assert config['substitutions']['_SANDBOX_IMAGE'].endswith('eval-scicode-sandbox:1.0.0')


@pytest.mark.parametrize('step_id,verdicts,status', [
    ('78.3', [False, False, False], 'known_upstream_defect'),
    ('70.8', [True, True, True, False], 'known_upstream_defect'),
    ('78.3', [False, False, False, False], 'failed'),  # An additional case fails.
    ('70.8', [False, True, True, False], 'failed'),
    ('70.8', [False, False, False, False], 'failed'),
    ('78.3', [True, False, False], 'failed'),  # A documented failure is missing.
    ('70.8', [False, True, True, True], 'failed'),  # Same count, different case.
    ('78.3', [True, True, True], 'passed'),
    ('70.8', [True, True, True, True], 'passed'),
    ('1.1', [False, False, False], 'failed'),
])
def test_known_defects_require_exact_authenticated_case_failures(monkeypatch, step_id, verdicts, status):
    def run(command, **kwargs):
        if SETUP in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps({'cwd':WORK,'key':KEY.hex()}))
        if RUNNER in command:
            return SimpleNamespace(returncode=0, stdout=signed(verdicts=verdicts))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(canonical.subprocess, 'run', run)
    result = canonical.check_step('fixture', {'step_number':step_id,'test_cases':['assert True']*len(verdicts)}, 3)
    assert result['status'] == status
    assert result['passed'] is (status == 'passed')
    assert result['failed_cases'] == [i for i, passed in enumerate(verdicts, 1) if not passed]
    assert canonical.summarize_results([result]) == {
        'passed':int(status == 'passed'), 'total':1,
        'known_upstream_defects':int(status == 'known_upstream_defect'),
        'unexpected_failures':int(status == 'failed'),
    }


@pytest.mark.parametrize('reason_flag', ['memory_exceeded', 'disk_exceeded', 'timeout', 'overflow', 'cleanup_failed', 'supervisor_error'])
def test_known_defect_does_not_mask_operational_failure(monkeypatch, reason_flag):
    def run(command, **kwargs):
        if SETUP in command:
            return SimpleNamespace(returncode=0, stdout=json.dumps({'cwd':WORK,'key':KEY.hex()}))
        if RUNNER in command:
            return SimpleNamespace(returncode=0, stdout=signed(verdicts=[False]*3, **{reason_flag:True}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(canonical.subprocess, 'run', run)
    result = canonical.check_step('fixture', {'step_number':'78.3','test_cases':['assert True']*3}, 3)
    assert result['status'] == 'failed'
    assert canonical.summarize_results([result])['unexpected_failures'] == 1


@pytest.mark.parametrize('unexpected', [False, True])
@pytest.mark.parametrize('peak_rss', [False, True])
def test_main_counts_all_steps_and_exits_only_for_unexpected_failures(monkeypatch, tmp_path, unexpected, peak_rss):
    output = tmp_path/'report.json'
    monkeypatch.setattr(canonical.sys, 'platform', 'linux')
    monkeypatch.setattr(canonical.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(canonical.sys, 'argv', ['canonical_check', '--output', str(output)] + (['--peak-rss'] if peak_rss else []))
    def check(code, step, timeout, dependencies, measure, targets_sha256=None):
        assert targets_sha256 is None
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


def test_verified_requests_bind_the_image_targets(monkeypatch):
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        if SETUP in command:return SimpleNamespace(returncode=0,stdout=json.dumps({'cwd':WORK,'key':KEY.hex()}))
        if RUNNER in command:return SimpleNamespace(returncode=0,stdout=signed(verdicts=[True,False,False]))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(canonical.subprocess,'run',run)
    step={'step_number':'1.1','test_cases':['assert True']*3}
    result=canonical.check_step('fixture',step,3,targets_sha256=canonical.VERIFIED_TARGETS_SHA256)
    assert json.loads(calls[0][1]['input'])['targets_sha256']==canonical.VERIFIED_TARGETS_SHA256
    assert result['status']=='known_verified_dev_target_change' and result['failed_cases']==[2,3]
    # Only the verified image carries the 1.1 target patch; elsewhere it is unexpected.
    assert canonical.check_step('fixture',step,3)['status']=='failed'
    assert canonical.summarize_results([result],verified=True)['known_verified_dev_target_changes']==1


@pytest.mark.parametrize('target_failure', [False, True])
def test_verified_main_checks_dev_steps_and_every_verified_target(monkeypatch, tmp_path, target_failure):
    from scicode.dataset import VERIFIED_TEST_DATA_SHA256, load_verified_records
    output = tmp_path/'report.json'
    monkeypatch.setattr(canonical.sys, 'platform', 'linux')
    monkeypatch.setattr(canonical.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(canonical.sys, 'argv', ['canonical_check', '--output', str(output), '--variant', 'verified'])
    assert canonical.VERIFIED_TARGETS_SHA256 == VERIFIED_TEST_DATA_SHA256
    def check(code, step, timeout, dependencies, measure, targets_sha256=None):
        assert targets_sha256 == VERIFIED_TEST_DATA_SHA256
        step_id = step['step_number']
        status = ('known_upstream_defect' if step_id in canonical.KNOWN_UPSTREAM_DEFECTS else
                  'known_verified_dev_target_change' if step_id in canonical.VERIFIED_DEV_TARGET_CHANGES else 'passed')
        return {'step':step_id, 'passed':status == 'passed', 'status':status, 'reason':'fixture'}
    checked = []
    def targets(step):
        checked.append(step['step_number'])
        return {'step':step['step_number'], 'cases':len(step['test_cases']),
                'targets_loaded': not (target_failure and step['step_number'] == '63.5')}
    monkeypatch.setattr(canonical, 'check_step', check)
    monkeypatch.setattr(canonical, 'check_targets', targets)
    assert canonical.main() == int(target_failure)
    report = json.loads(output.read_text())
    assert report['image'] == canonical.VERIFIED_IMAGE and report['variant'] == 'verified'
    assert (report['total'], report['passed'], report['known_upstream_defects'],
            report['known_verified_dev_target_changes'], report['unexpected_failures']) == (50, 47, 2, 1, 0)
    tested = [s['step_number'] for r in load_verified_records() for s in r['sub_steps'] if s['test_cases']]
    assert checked == tested and report['verified_target_steps'] == len(tested) == 286
    assert report['verified_target_failures'] == (['63.5'] if target_failure else [])


def test_verified_cloudbuild_runs_the_verified_variant():
    config=yaml.safe_load(Path('scripts/cloudbuild-canonical-verified.yaml').read_text())
    command=config['steps'][0]['args'][-1]
    for text in ['--cap-add SYS_PTRACE','--network none','--read-only','--memory 6g','--peak-rss',
                 '--replays', 'scripts/canonical_check.py --variant verified']:
        assert text in command
    assert config['substitutions']['_SANDBOX_IMAGE'] == canonical.VERIFIED_IMAGE


@pytest.mark.parametrize('variant', ['scicode', 'verified'])
@pytest.mark.parametrize('replay_failure', [False, True])
def test_replays_must_all_pass(monkeypatch, tmp_path, variant, replay_failure):
    output = tmp_path/'report.json'
    monkeypatch.setattr(canonical.sys, 'platform', 'linux')
    monkeypatch.setattr(canonical.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(canonical.sys, 'argv', ['canonical_check', '--output', str(output), '--variant', variant,
                                                '--replays', '--peak-rss'])
    def check(code, step, timeout, dependencies, measure, targets_sha256=None):
        step_id = step['step_number']
        status = ('known_upstream_defect' if step_id in canonical.KNOWN_UPSTREAM_DEFECTS else
                  'known_verified_dev_target_change' if targets_sha256 and step_id in canonical.VERIFIED_DEV_TARGET_CHANGES
                  else 'passed')
        return {'step':step_id, 'passed':status == 'passed', 'status':status, 'reason':'fixture',
                'peak_candidate_rss_bytes':1, 'peak_executor_vm_bytes':2}
    seen = []
    def replays(verified, timeout, targets_sha256, directory):
        seen.append((verified, targets_sha256))
        return [{'step':s, 'passed':not (replay_failure and s == '63.2'), 'peak_candidate_rss_bytes':3,
                 'peak_executor_vm_bytes':1044918272} for s in canonical.runpy.run_path('scripts/reply_replays.py')['STEPS']]
    monkeypatch.setattr(canonical, 'check_step', check)
    monkeypatch.setattr(canonical, 'check_targets', lambda step: {'step':step['step_number'], 'targets_loaded':True})
    monkeypatch.setattr(canonical, 'run_replays', replays)
    assert canonical.main() == int(replay_failure)
    report = json.loads(output.read_text())
    assert seen == [(variant == 'verified', canonical.VERIFIED_TARGETS_SHA256 if variant == 'verified' else None)]
    assert report['replay_summary'] == {'passed': 6 - int(replay_failure), 'total': 6,
                                        'peak_candidate_rss_bytes': 3, 'peak_executor_vm_bytes': 1044918272}
    assert report['peak_executor_vm_bytes'] == 2


def test_population_records_match_the_packaged_tasks():
    from scicode.dataset import load_records, load_verified_records
    assert canonical.population_records(False) == load_records()
    assert canonical.population_records(True) == load_verified_records()


def test_requests_carry_the_packaged_reply_limit(monkeypatch):
    from scicode.execution import REPLY_LIMIT, OUTPUT_LIMIT
    calls=[]
    def run(command,**kwargs):
        calls.append(kwargs)
        if SETUP in command:return SimpleNamespace(returncode=0,stdout=json.dumps({'cwd':WORK,'key':KEY.hex()}))
        if RUNNER in command:return SimpleNamespace(returncode=0,stdout=signed(verdicts=[True], peak_candidate_rss_bytes=1, peak_executor_vm_bytes=7))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(canonical.subprocess,'run',run)
    result = canonical.check_step('x',{'step_number':'1.1','test_cases':['assert True']},3,peak_rss=True)
    request = json.loads(calls[0]['input'])
    assert (request['reply_limit'], request['output_limit']) == (REPLY_LIMIT, OUTPUT_LIMIT)
    assert result['peak_executor_vm_bytes'] == 7
