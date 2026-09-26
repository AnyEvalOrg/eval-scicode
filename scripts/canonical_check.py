"""Run every dev ground truth through the actual SETUP/RUNNER in a disposable container.

--variant verified runs the same 50 dev steps in the SciCode-Verified image (whose HDF5
also holds the dev targets), binding requests to its target marker, and then loads the
targets of every tested SciCode-Verified step under the trusted executor's address-space
limit. SciCode-Verified ships no test-set reference solutions, so no test step can be
executed with a reference solution here.

--replays additionally runs scripts/reply_replays.py for the image's own test population
(original targets in the scicode image, corrected targets in the verified image): authored
candidates that return each stored target at the largest measured reply sizes, through the
real SETUP/RUNNER and executor limits. Every replay case must pass.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'us-central1-docker.pkg.dev/openevalz-sbx-84737/openevalz/eval-scicode-sandbox:1.0.0'
VERIFIED_IMAGE = 'us-central1-docker.pkg.dev/openevalz-sbx-84737/openevalz/eval-scicode-verified-sandbox:1.0.0'
VERIFIED_TARGETS_SHA256 = '8fb6e575b7b6dda5e48b04dea338fc6af4fe185774b8f19221c96945df9b4142'
# Loads one step's targets exactly as the trusted executor does, under its RLIMIT_AS
# (comparison_worker.ADDRESS_SPACE), and prints only the count of loaded cases (never values).
TARGET_LOADER = r'''
import resource, sys
limit = int(sys.argv[3])
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
sys.path.insert(0, "/opt/scicode/runtime")
import process_data
process_data.H5PY_FILE = "/opt/scicode/test_data.h5"
print(len(process_data.process_hdf5_to_tuple(sys.argv[1], int(sys.argv[2]))))
'''
# One-based case indices whose reference code fails unchanged stored targets.
KNOWN_UPSTREAM_DEFECTS = {'78.3': frozenset({1, 2, 3}), '70.8': frozenset({4})}
# SciCode-Verified's internal target patch targets/1.json rewrote dev step 1.1 cases 2-3 (dev
# problem 1 is outside its 64-problem population). The unchanged dev reference reproduces
# the original values, not the patched ones. Only the verified image carries this change.
VERIFIED_DEV_TARGET_CHANGES = {'1.1': frozenset({2, 3})}


def check_step(code, step, timeout, dependencies='', peak_rss=False, targets_sha256=None):
    changed = VERIFIED_DEV_TARGET_CHANGES if targets_sha256 is not None else {}
    runner = runpy.run_path(str(ROOT/'scicode/sandbox_runner.py'))
    receipts = runpy.run_path(str(ROOT/'scicode/receipts.py'))
    execution = runpy.run_path(str(ROOT/'scicode/execution.py'))
    request = {'code': code, 'step_id': step['step_number'], 'tests': step['test_cases'],
               'dependencies': dependencies,
               'timeout': timeout, 'output_limit': execution['OUTPUT_LIMIT'],
               'reply_limit': execution['REPLY_LIMIT']}
    if peak_rss:
        request['measure_peak_rss'] = True
    if targets_sha256 is not None:
        request['targets_sha256'] = targets_sha256
    setup = subprocess.run([sys.executable,'-I','-c',runner['SETUP']], input=json.dumps(request),
                           capture_output=True,text=True,timeout=10)
    if setup.returncode:
        raise RuntimeError('Setup failed')
    setup = json.loads(setup.stdout)
    try:
        result = subprocess.run(['timeout','-s','KILL',str(timeout+30),sys.executable,'-I','-c',runner['RUNNER'],setup['cwd']],
                                capture_output=True,text=True,timeout=timeout+35)
        receipt = receipts['verify_receipt'](result.stdout,bytes.fromhex(setup['key']))
        if receipt is None or receipt['cwd'] != setup['cwd']:
            raise RuntimeError('Missing authenticated receipt')
        failure = receipts['receipt_failure'](receipt)
        if len(receipt['verdicts']) != len(step['test_cases']):
            raise RuntimeError('Invalid test count')
    finally:
        for command in (runner['CLEANUP_COMMAND'],runner['QUIESCENCE_COMMAND']):
            cleanup = subprocess.run(command,capture_output=True,timeout=10)
            if cleanup.returncode not in ((0,1) if command == runner['CLEANUP_COMMAND'] else (0,)):
                raise RuntimeError('Cleanup failed')
    failed_cases = [index for index, passed in enumerate(receipt['verdicts'], start=1) if not passed]
    result = {'step':step['step_number'],'passed':failure is None,'reason':failure or 'all tests passed',
              'failed_cases':failed_cases}
    result['status'] = ('passed' if failure is None else 'known_upstream_defect'
                        if failure == 'test comparison failed'
                        and set(failed_cases) == KNOWN_UPSTREAM_DEFECTS.get(result['step'])
                        else 'known_verified_dev_target_change'
                        if failure == 'test comparison failed'
                        and set(failed_cases) == changed.get(result['step'])
                        else 'failed')
    if peak_rss:
        result['peak_candidate_rss_bytes'] = receipt['peak_candidate_rss_bytes']
        if 'peak_executor_vm_bytes' in receipt:
            result['peak_executor_vm_bytes'] = receipt['peak_executor_vm_bytes']
    return result


def check_targets(step):
    """Whether every test case of a step has loadable targets in the image."""
    try:
        limit = runpy.run_path(str(ROOT/'scicode/comparison_worker.py'))['ADDRESS_SPACE']
        loaded = subprocess.run([sys.executable, '-I', '-c', TARGET_LOADER, step['step_number'],
                                 str(len(step['test_cases'])), str(limit)],
                                capture_output=True, text=True, timeout=120)
        ok = loaded.returncode == 0 and loaded.stdout.strip() == str(len(step['test_cases']))
    except subprocess.TimeoutExpired:
        ok = False
    return {'step': step['step_number'], 'cases': len(step['test_cases']), 'targets_loaded': ok}


def verified_test_steps():
    data = ROOT/'scicode/data'
    manifest = json.loads((data/'verified_manifest.json').read_text())
    blob = (data/'problems_verified_test.jsonl.gz').read_bytes()
    assert hashlib.sha256(blob).hexdigest() == manifest['artifact_sha256']
    raw = gzip.decompress(blob)
    assert hashlib.sha256(raw).hexdigest() == manifest['source_sha256']
    return [step for line in raw.splitlines() for step in json.loads(line)['sub_steps']]


def summarize_results(results, verified=False):
    summary = {'passed':sum(r['passed'] for r in results), 'total':len(results),
               'known_upstream_defects':sum(r['status'] == 'known_upstream_defect' for r in results),
               'unexpected_failures':sum(r['status'] == 'failed' for r in results)}
    if verified:
        summary['known_verified_dev_target_changes'] = sum(
            r['status'] == 'known_verified_dev_target_change' for r in results)
    if results and all('peak_candidate_rss_bytes' in r for r in results):
        summary['peak_candidate_rss_bytes'] = max(r['peak_candidate_rss_bytes'] for r in results)
    if results and all('peak_executor_vm_bytes' in r for r in results):
        summary['peak_executor_vm_bytes'] = max(r['peak_executor_vm_bytes'] for r in results)
    return summary


def population_records(verified):
    data = ROOT/'scicode/data'
    if verified:
        info = json.loads((data/'verified_manifest.json').read_text())
        blob = (data/'problems_verified_test.jsonl.gz').read_bytes()
        source = info['source_sha256']
    else:
        info = json.loads((data/'manifest.json').read_text())['splits']['all']
        blob = (data/'problems_all.jsonl.gz').read_bytes()
        source = info['source_sha256']
    assert hashlib.sha256(blob).hexdigest() == info['artifact_sha256']
    raw = gzip.decompress(blob)
    assert hashlib.sha256(raw).hexdigest() == source
    return [json.loads(line) for line in raw.splitlines()]


def run_replays(verified, timeout, targets_sha256, directory):
    """Largest-reply exact-target replays for the image's own population (all must pass)."""
    import pickle
    replays = runpy.run_path(str(ROOT/'scripts/reply_replays.py'))
    sys.path.insert(0, '/opt/scicode/runtime')
    import process_data
    process_data.H5PY_FILE = '/opt/scicode/test_data.h5'
    records = {r['problem_id']: r for r in population_records(verified)}
    # Readable by the candidate UID; outside the watched /tmp tree.
    directory.mkdir(parents=True, exist_ok=True)
    for folder in (directory.parent, directory):
        os.chmod(folder, 0o755)
    results = []
    for step_id in replays['STEPS']:
        record = records[step_id.split('.')[0]]
        step = next(s for s in record['sub_steps'] if s['step_number'] == step_id)
        path = directory/f'{step_id}.pickle'
        path.write_bytes(pickle.dumps(process_data.process_hdf5_to_tuple(step_id, len(step['test_cases']))))
        os.chmod(path, 0o644)
        try:
            result = check_step(replays['candidate_code'](record['required_dependencies'], step_id, path),
                                step, timeout, record['required_dependencies'], True, targets_sha256)
        finally:
            path.unlink()
        result['status'] = 'passed' if result['passed'] else 'failed'
        results.append(result)
        print(json.dumps({'replay': result}), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'.build/canonical-result.json')
    parser.add_argument('--variant',choices=['scicode','verified'],default='scicode')
    parser.add_argument('--image')
    parser.add_argument('--timeout',type=int,default=300)
    parser.add_argument('--replays',action='store_true',
                        help='Also run the largest-reply exact-target replays for the image population')
    parser.add_argument('--peak-rss',action='store_true',
                        help='Report watchdog-observed aggregate candidate peak RSS in bytes per step and overall')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.error('Requires root in a disposable Linux sandbox image')
    verified = args.variant == 'verified'
    image = args.image or (VERIFIED_IMAGE if verified else IMAGE)
    targets_sha256 = VERIFIED_TARGETS_SHA256 if verified else None
    try:
        data = ROOT/'scicode/data'
        manifest = json.loads((data/'manifest.json').read_text())
        blob = (data/'problems_dev.jsonl.gz').read_bytes()
        assert hashlib.sha256(blob).hexdigest() == manifest['splits']['dev']['artifact_sha256']
        raw = gzip.decompress(blob)
        assert hashlib.sha256(raw).hexdigest() == '193968aff23b7ed931c8f6d196b611e2f6694adb49e6cc085444a60ad2fc4f7b'
        records = [json.loads(line) for line in raw.splitlines()]
        results = []
        for record in records:
            codes = [record['required_dependencies'], 'from test_util import are_dicts_close, cmp_tuple_or_list']
            for step in record['sub_steps']:
                codes.append(step['ground_truth_code'])
                result = check_step('\n'.join(codes), step, args.timeout, record['required_dependencies'], args.peak_rss,
                                    targets_sha256)
                results.append(result)
                print(json.dumps(result),flush=True)
        report = {'image':image,'variant':args.variant,'dataset_revision':manifest['revision'],'results':results,
                  **summarize_results(results, verified)}
        if verified:
            targets = [check_targets(step) for step in verified_test_steps() if step['test_cases']]
            for target in targets:
                print(json.dumps(target), flush=True)
            report['verified_target_steps'] = len(targets)
            report['verified_target_failures'] = [t['step'] for t in targets if not t['targets_loaded']]
        if args.replays:
            replayed = run_replays(verified, args.timeout, targets_sha256, args.output.parent/'replays')
            report['replays'] = replayed
            report['replay_summary'] = {'passed': sum(r['passed'] for r in replayed), 'total': len(replayed),
                                        'peak_candidate_rss_bytes': max(r['peak_candidate_rss_bytes'] for r in replayed),
                                        'peak_executor_vm_bytes': max(r.get('peak_executor_vm_bytes', 0) for r in replayed)}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k not in ('results', 'replays')}))
        replays_ok = not args.replays or report['replay_summary']['passed'] == report['replay_summary']['total']
        return 0 if (report['unexpected_failures'] == 0 and not report.get('verified_target_failures')
                     and replays_ok) else 1
    except Exception:
        print('Canonical check failed; private details withheld.',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
