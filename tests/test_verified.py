"""scicode/scicode_verified: SciCode-Verified v2 data, image binding and protocol parity."""
import ast
import asyncio
import builtins
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import pickle
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import yaml
from inspect_ai.model import ChatMessageAssistant, ChatMessageUser
from inspect_ai.scorer import Target
from inspect_ai.util import ExecResult
from inspect_ai.util._sandbox.events import SandboxEnvironmentProxy

import scicode.dataset as dataset
import scicode.scoring as scoring
from scicode.dataset import (VERIFIED_TEST_DATA_SHA256, get_verified_dataset, load_records,
                             load_verified_records, verified_manifest)
from scicode.execution import execution_request
from scicode import prompt_templates as prompts
from scicode.receipts import receipt_failure
from scicode.sandbox_runner import RUNNER, SETUP
from scicode.solver import composed_code, solve_scicode_problem
from scicode.task import scicode, scicode_verified
from test_scoring import KEY, WORK, signed

VERIFIED_IDS = ['5', '8', '9', '11', '12', '13', '14', '15', '16', '17', '18', '20', '21', '22', '23', '24',
                '25', '26', '27', '28', '30', '31', '32', '33', '34', '35', '36', '37', '39', '40', '41', '42',
                '43', '45', '46', '48', '50', '52', '53', '54', '55', '56', '57', '58', '59', '60', '61', '62',
                '63', '64', '65', '66', '67', '68', '69', '71', '72', '73', '74', '75', '76', '77', '79', '80']
IMAGE = 'us-central1-docker.pkg.dev/openevalz-sbx-84737/openevalz/eval-scicode-verified-sandbox:1.0.0'
CLEANED_H5 = Path('scicode/test_data_cleaned.h5')


def steps_of(records):
    return [s for r in records for s in r['sub_steps']]


def test_manifest_pins_release_and_artifact():
    info = verified_manifest()
    assert info['dataset'] == 'flyingwagner/scicode-verified' and info['version'] == 'v2'
    assert info['revision'] == dataset.VERIFIED_REVISION == 'ddab4a92f8d80a7113ab946628e994b52354d838'
    assert info['huggingface'] == {'dataset':'shhu2001/SciCode-Verified',
                                   'revision':'eea11a866be6860725258702b39ef8651ed26abd'}
    assert info['derived_from']['revision'] == dataset.SCICODE_GITHUB_REVISION
    blob = Path('scicode/data/problems_verified_test.jsonl.gz').read_bytes()
    raw = gzip.decompress(blob)
    assert hashlib.sha256(blob).hexdigest() == info['artifact_sha256']
    assert hashlib.sha256(raw).hexdigest() == info['source_sha256'] == dataset.VERIFIED_CHECKSUM
    # The release manifest's problems_test_jsonl_md5 (also printed on the dataset card).
    assert hashlib.md5(raw).hexdigest() == info['source_md5'] == '5c604d8dbf52642bd94e13b92c8f52eb'
    assert info['test_data'] == {'file':'test_data_cleaned.h5', 'size':1108078257,
                                 'md5':'2b41a7df40ddc23ce651ec05b8ecb6f8',
                                 'sha256':VERIFIED_TEST_DATA_SHA256}
    assert (info['count'], info['subproblems'], info['scored_subproblems']) == (64, 290, 287)
    assert info['unscored_subproblems'] == ['13.6', '62.1', '76.3']


def test_ids_counts_and_population():
    records = load_verified_records()
    ids = [r['problem_id'] for r in records]
    assert ids == VERIFIED_IDS == verified_manifest()['task_ids']
    assert all(type(i) is str for i in ids)
    # Exactly the original test split minus problem 2 (underdetermined; dropped upstream).
    assert set(ids) == {r['problem_id'] for r in load_records()} - {'2'}
    assert len(steps_of(records)) == 290
    assert [s['step_number'] for s in steps_of(records) if not s['test_cases']] == \
        verified_manifest()['untested_subproblems'] == ['13.6', '62.1', '72.6', '76.3']
    original = {r['problem_id']: r for r in load_records()}
    for record in records:
        assert [s['step_number'] for s in record['sub_steps']] == \
            [s['step_number'] for s in original[record['problem_id']]['sub_steps']]


def test_samples_carry_only_public_prompts():
    records = load_verified_records()
    samples = list(get_verified_dataset())
    assert len(samples) == 64
    for record, sample in zip(records, samples):
        assert sample.id == record['problem_id']
        assert sample.input == record['problem_description_main']
        assert sample.metadata in ({}, None) and sample.target == ''
        assert not sample.files and not sample.setup
        exported = json.dumps(sample.model_dump(mode='json'))
        assert all(k not in exported for k in ['ground_truth_code', 'test_cases', 'general_solution', 'general_tests'])
    # The release ships no test-set reference code at all.
    assert all(r['general_solution'] is None for r in records)
    assert all(s['ground_truth_code'] is None for s in steps_of(records))


def test_tampered_or_mismatched_artifacts_are_refused(monkeypatch):
    info = verified_manifest()
    for key, value in [('artifact_sha256', '0'*64), ('revision', 'main'), ('task_ids', info['task_ids'][::-1])]:
        monkeypatch.setattr(dataset, 'verified_manifest', lambda key=key, value=value: {**info, key: value})
        with pytest.raises(RuntimeError, match='details withheld'):
            load_verified_records()
    monkeypatch.setattr(dataset, 'verified_manifest', lambda: {**info, 'test_data': {**info['test_data'], 'sha256': '0'*64}})
    with pytest.raises(RuntimeError):
        load_verified_records()


def changed_step():
    original = {s['step_number']: s for s in steps_of(load_records())}
    for record in load_verified_records():
        for index, step in enumerate(record['sub_steps']):
            if step['step_description_prompt'] != original[step['step_number']]['step_description_prompt']:
                return record, index, step, original[step['step_number']]
    raise AssertionError('no corrected prompt found')


@pytest.mark.parametrize('background', [False, True])
def test_solver_sends_corrected_prompts_through_the_same_templates(background):
    record, index, step, original = changed_step()
    state = SimpleNamespace(sample_id=record['problem_id'],
                            messages=[ChatMessageUser(content=record['problem_description_main'])])
    sent = []

    async def generate(state):
        sent.append(state.messages[-1].text)
        state.messages.append(ChatMessageAssistant(content=f'```python\n# step {len(sent)}\n```'))
        return state
    asyncio.run(solve_scicode_problem(background, variant='verified')(state, generate))
    template = prompts.SUBPROBLEM_PROMPT_PROVIDE_BACKGROUND if background else prompts.SUBPROBLEM_PROMPT
    initial = prompts.INITIAL_PROMPT_PROVIDE_BACKGROUND if background else prompts.INITIAL_PROMPT
    assert state.messages[0].text == initial.format(required_dependencies=record['required_dependencies'])
    assert sent == [template.format(**s) for s in record['sub_steps']]
    assert sent[index] != template.format(**original)
    for i, s in enumerate(record['sub_steps']):
        code = composed_code(record, state.messages, s)
        assert all(f'# step {j+1}' in code for j in range(i+1)) and f'# step {i+2}' not in code


def test_unknown_variant_is_refused():
    with pytest.raises(ValueError):
        solve_scicode_problem(False, variant='other')
    with pytest.raises(ValueError):
        scoring.verify(300, variant='other')


class CapturingSandbox:
    def __init__(self):
        self.requests = []

    async def exec(self, cmd, input=None, **kwargs):
        if SETUP in cmd:
            self.requests.append(json.loads(input))
            return ExecResult(True, 0, json.dumps({'cwd': WORK, 'key': KEY.hex()}), '')
        if RUNNER in cmd:
            return ExecResult(True, 0, signed(verdicts=[True]*len(self.requests[-1]['tests'])), '')
        return ExecResult(True, 0, '', '')


@pytest.mark.parametrize('variant', ['scicode', 'verified'])
def test_scorer_binds_verified_targets_and_uses_verified_tests(monkeypatch, variant):
    records = load_verified_records() if variant == 'verified' else load_records()
    record = next(r for r in records if r['problem_id'] == '12')
    fake = CapturingSandbox()
    monkeypatch.setattr(scoring, 'sandbox', lambda: SandboxEnvironmentProxy(fake))
    replies = [ChatMessageAssistant(content=f'```python\ndef f{i}(): pass\n```') for i in range(len(record['sub_steps']))]
    state = SimpleNamespace(sample_id='12', messages=replies, token_limit=None, token_usage=0)
    score = asyncio.run(scoring.verify(300, variant=variant)(state, Target('')))
    assert score.value == 'C'
    assert [r['tests'] for r in fake.requests] == [s['test_cases'] for s in record['sub_steps']]
    for request in fake.requests:
        assert request.get('targets_sha256') == (VERIFIED_TEST_DATA_SHA256 if variant == 'verified' else None)
        assert ('targets_sha256' in request) is (variant == 'verified')


def test_default_request_shape_has_no_target_digest():
    step = load_records()[0]['sub_steps'][0]
    assert set(execution_request('x', step)) == {'code', 'step_id', 'tests', 'dependencies', 'timeout',
                                                 'output_limit', 'reply_limit'}
    assert execution_request('x', step, targets_sha256='ab')['targets_sha256'] == 'ab'


@pytest.mark.parametrize('record', load_verified_records(), ids=lambda r: r['problem_id'])
def test_every_verified_test_is_transmitted_unchanged(record):
    for step in record['sub_steps']:
        request = execution_request('def candidate(): return 0', step, dependencies=record['required_dependencies'],
                                    targets_sha256=VERIFIED_TEST_DATA_SHA256)
        assert request['tests'] is step['test_cases']
        assert request['dependencies'] == record['required_dependencies']


def test_verified_references_never_flow_into_candidate_calls():
    """Corpus audit: no target-derived value is an argument of a candidate-defined callee.

    Unlike the original corpus, corrected tests use builtins (sorted/set/abs/zip), test-local
    helpers and lambdas, and attribute calls on target values. All of those run in the trusted
    executor; only callees defined by the problem's function headers (or unknown names) would
    send references to the candidate, and there are none.
    """
    exceptions, cases, reference_calls = [], 0, 0
    trusted_roots = {'np', 'scipy', 'sp', 'la', 'signal', 'cmp_tuple_or_list', 'are_dicts_close',
                     'are_csc_matrix_close'}
    for record in load_verified_records():
        candidate = {name for s in record['sub_steps']
                     for name in re.findall(r'^(?:def|class)\s+(\w+)', s['function_header'], re.M)}
        for step in record['sub_steps']:
            for index, source in enumerate(step['test_cases'], 1):
                cases += 1
                tree = ast.parse(source)
                tainted = {'target'}

                def depends(node):
                    return any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in tainted
                               for n in ast.walk(node))
                while True:
                    before = set(tainted)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Assign) and depends(node.value):
                            tainted.update(n.id for dst in node.targets for n in ast.walk(dst) if isinstance(n, ast.Name))
                        if isinstance(node, (ast.For, ast.comprehension)) and depends(node.iter):
                            tainted.update(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name))
                    if tainted == before:
                        break
                local = ({n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
                         | {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Lambda)
                            for t in n.targets if isinstance(t, ast.Name)})
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not (
                            any(depends(a) for a in node.args) or any(depends(k.value) for k in node.keywords)):
                        continue
                    reference_calls += 1
                    root = node.func
                    while isinstance(root, ast.Attribute):
                        root = root.value
                    name = root.id if isinstance(root, ast.Name) else None
                    if (name is None or name in candidate
                            or not (name in trusted_roots or name in local or name in tainted or hasattr(builtins, name))):
                        exceptions.append((step['step_number'], index, ast.unparse(node)))
    assert (cases, reference_calls) == (885, 939)
    assert exceptions == [], exceptions


def test_task_registration_and_sandbox_selection():
    task = scicode_verified(sandbox_type='docker')
    assert [s.id for s in task.dataset] == VERIFIED_IDS
    assert task.epochs == 1 and task.version == '1.0.0'
    assert task.metadata == {'metric': 'main-problem pass@1', 'dataset_provenance': verified_manifest()}
    assert task.config.temperature is None
    assert Path(task.sandbox.config).name == 'compose-verified.yaml'
    k8s = scicode_verified().sandbox.config
    assert Path(k8s.values).name == 'values-verified.yaml' and Path(k8s.chart).name == 'chart'
    assert Path(scicode_verified(anyeval_chart=False).sandbox.config).name == 'values-verified.yaml'
    # scicode/scicode keeps its own image configuration.
    assert Path(scicode().sandbox.config.values).name == 'values.yaml'
    assert Path(scicode(sandbox_type='docker').sandbox.config).name == 'compose.yaml'
    for bad in (0, 3601, '300', 1.5):
        with pytest.raises(ValueError):
            scicode_verified(timeout=bad, sandbox_type='docker')


@pytest.mark.parametrize('release', ['first', 'second'])
def test_verified_helm_render_uses_the_verified_image(release):
    config = scicode_verified().sandbox.config
    result = subprocess.run([shutil.which('helm'), 'template', release, str(config.chart), '-n', 'anyeval-sandbox',
                             '-f', str(config.values)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    documents = [d for d in yaml.safe_load_all(result.stdout) if d]
    pod = next(d for d in documents if d['kind'] == 'Pod')
    policy = next(d for d in documents if d['kind'] == 'NetworkPolicy')
    assert pod['spec']['containers'][0]['image'] == IMAGE
    assert pod['spec']['runtimeClassName'] == 'gvisor'
    assert pod['spec']['containers'][0]['resources']['limits']['memory'] == '6Gi'
    assert policy['spec']['ingress'] == policy['spec']['egress'] == []


def test_verified_sandbox_configs_differ_only_by_image():
    values = yaml.safe_load(Path('scicode/values.yaml').read_text())
    verified = yaml.safe_load(Path('scicode/values-verified.yaml').read_text())
    assert verified['services']['default'].pop('image') == IMAGE
    values['services']['default'].pop('image')
    assert verified == values
    compose = yaml.safe_load(Path('scicode/compose.yaml').read_text())
    verified_compose = yaml.safe_load(Path('scicode/compose-verified.yaml').read_text())
    assert verified_compose['services']['default'].pop('image') == '${SCICODE_VERIFIED_SANDBOX_IMAGE:-' + IMAGE + '}'
    compose['services']['default'].pop('image')
    assert verified_compose == compose


def test_verified_image_bakes_and_marks_the_pinned_targets():
    original = Path('scicode/Dockerfile').read_text().splitlines()
    text = Path('scicode/verified.Dockerfile').read_text()
    digest = VERIFIED_TEST_DATA_SHA256
    assert text.startswith('FROM python:3.12-slim-trixie')
    assert 'COPY scicode/test_data_cleaned.h5 /opt/scicode/test_data.h5' in text
    assert f"echo '{digest}  /opt/scicode/test_data.h5' | sha256sum -c -" in text
    assert 'chown root:root /opt/scicode/test_data.h5 && chmod 0400 /opt/scicode/test_data.h5' in text
    assert f"echo '{digest}' > /opt/scicode/test_data.sha256" in text
    assert 'chown root:root /opt/scicode/test_data.sha256 && chmod 0444 /opt/scicode/test_data.sha256' in text
    # Runtime, packages and entry point are the original image's, line for line.
    kept = [line for line in original if 'test_data' not in line]
    assert [line for line in kept if line in text.splitlines()] == kept
    assert 'test_data.sha256' not in Path('scicode/Dockerfile').read_text()
    build = yaml.safe_load(Path('scripts/cloudbuild-image-verified.yaml').read_text())
    assert build['steps'][0]['args'][:3] == ['build', '-f', 'scicode/verified.Dockerfile']
    assert build['substitutions']['_SANDBOX_IMAGE'] == IMAGE
    spec = importlib.util.spec_from_file_location('fetch_verified', Path('scripts/fetch_verified_test_data.py'))
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)
    assert (fetch.SHA256, fetch.MD5) == (digest, verified_manifest()['test_data']['md5'])
    assert fetch.HF_REVISION == verified_manifest()['huggingface']['revision'] and fetch.HF_REVISION in fetch.URL
    ignored = Path('.gitignore').read_text()
    assert '**/test_data_cleaned.h5' in ignored


def test_anyeval_manifest_lists_both_tasks():
    catalog = json.loads(Path('anyeval.json').read_text())
    assert catalog['tasks'] == [{'name': 'scicode', 'samples': 65}, {'name': 'scicode_verified', 'samples': 64}]
    assert catalog['total_samples'] == 129
    sources = [a['source'] for a in catalog['external_assets']]
    assert IMAGE in sources


# Candidate replays of the exact targets for the corrected tests whose constructs are new
# (builtins, test-local helpers/lambdas, dict and object results, sparse operator dicts).
# Each exercises the real two-process proxy executor with the real SciCode-Verified targets:
# a candidate returning the correct values must pass. They are harness checks, not
# solutions; SciCode-Verified ships no test-set reference code.
REPLAYS = {
    '12.2': 'def f_Schrod(*a, **k):\n    return np.zeros(3)\ndef Numerov(*a, **k):\n    return _next()',
    '12.4': 'def shoot(*a, **k):\n    return _next()',
    '31.3': 'def ica(*a, **k):\n    return _next()',
    '33.2': 'def compute_chern_number(*a, **k):\n    return _next()',
    '33.3': 'def compute_chern_number_grid(*a, **k):\n    return tuple(_next())',
    '53.4': 'def predator_prey(*a, **k):\n    return tuple(_next())',
    '62.5': ('class Block:\n    def __init__(self, length, basis_size, operator_dict):\n'
             '        self.length = length\n        self.basis_size = basis_size\n'
             '        self.operator_dict = operator_dict\n'
             'def block_initial(model_d):\n    return Block(1, model_d, {})\n'
             'def dmrg_module(sys, env, m, model_d):\n    a, b, c, d = _next()\n    return Block(a, b, c), d'),
    '73.6': 'def ringdstar(*a, **k):\n    return _next()',
    '73.7': 'def hkl_pairs(*a, **k):\n    return tuple(_next())',
    '73.8': 'def Umat_p(*a, **k):\n    return tuple(_next())',
    # 73.9 case 3 asserts that the case-1 indices are integral; its target is that boolean.
    '73.9': ('def auto_index(*a, **k):\n    value = _next()\n'
             '    return np.round(_T[0]) if _I[0] == 3 else value'),
}


@pytest.mark.parametrize('step_id', sorted(REPLAYS, key=lambda s: tuple(map(int, s.split('.')))))
def test_corrected_test_constructs_pass_exact_replays(tmp_path, step_id):
    if not CLEANED_H5.exists():
        pytest.skip('SciCode-Verified HDF5 targets not available locally (scripts/fetch_verified_test_data.py)')
    from proxy_harness import run_signed
    from scicode import process_data
    record = next(r for r in load_verified_records() if r['problem_id'] == step_id.split('.')[0])
    step = next(s for s in record['sub_steps'] if s['step_number'] == step_id)
    process_data.H5PY_FILE = str(CLEANED_H5)
    targets = process_data.process_hdf5_to_tuple(step_id, len(step['test_cases']))
    tests = step['test_cases']
    replay = tmp_path / 'replay.pickle'
    replay.write_bytes(pickle.dumps(targets))
    code = '\n'.join([record['required_dependencies'], 'import pickle as _pickle',
                      f'_T = _pickle.loads(open({str(replay)!r}, "rb").read())', '_I = [0]',
                      'def _next():\n    _I[0] += 1\n    return _T[_I[0] - 1]', REPLAYS[step_id]])
    result = run_signed(tmp_path, code, tests, targets,
                        dependencies=record['required_dependencies'], timeout=300)
    failed = [i for i, passed in enumerate(result['verdicts'], 1) if not passed]
    assert receipt_failure(result) is None, f'{step_id}: failed cases {failed}; receipt={result}'
