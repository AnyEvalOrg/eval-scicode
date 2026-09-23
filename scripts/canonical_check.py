"""Run every dev ground truth through the actual SETUP/RUNNER in a disposable container."""
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
# Reference code fails unchanged stored targets in the pinned image.
KNOWN_UPSTREAM_DEFECTS = frozenset({'78.3', '70.8'})


def check_step(code, step, timeout, dependencies='', peak_rss=False):
    runner = runpy.run_path(str(ROOT/'scicode/sandbox_runner.py'))
    receipts = runpy.run_path(str(ROOT/'scicode/receipts.py'))
    request = {'code': code, 'step_id': step['step_number'], 'tests': step['test_cases'],
               'dependencies': dependencies,
               'timeout': timeout, 'output_limit': 32*1024*1024}
    if peak_rss:
        request['measure_peak_rss'] = True
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
    result = {'step':step['step_number'],'passed':failure is None,'reason':failure or 'all tests passed'}
    result['status'] = ('passed' if failure is None else 'known_upstream_defect'
                        if result['step'] in KNOWN_UPSTREAM_DEFECTS and failure == 'test comparison failed'
                        else 'failed')
    if peak_rss:
        result['peak_candidate_rss_bytes'] = receipt['peak_candidate_rss_bytes']
    return result


def summarize_results(results):
    summary = {'passed':sum(r['passed'] for r in results), 'total':len(results),
               'known_upstream_defects':sum(r['status'] == 'known_upstream_defect' for r in results),
               'unexpected_failures':sum(r['status'] == 'failed' for r in results)}
    if results and all('peak_candidate_rss_bytes' in r for r in results):
        summary['peak_candidate_rss_bytes'] = max(r['peak_candidate_rss_bytes'] for r in results)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'.build/canonical-result.json')
    parser.add_argument('--image',default=IMAGE)
    parser.add_argument('--timeout',type=int,default=300)
    parser.add_argument('--peak-rss',action='store_true',
                        help='Report watchdog-observed aggregate candidate peak RSS in bytes per step and overall')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.error('Requires root in a disposable Linux sandbox image')
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
                result = check_step('\n'.join(codes), step, args.timeout, record['required_dependencies'], args.peak_rss)
                results.append(result)
                print(json.dumps(result),flush=True)
        report = {'image':args.image,'dataset_revision':manifest['revision'],'results':results,
                  **summarize_results(results)}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k != 'results'}))
        return 0 if report['unexpected_failures'] == 0 else 1
    except Exception:
        print('Canonical check failed; private details withheld.',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
