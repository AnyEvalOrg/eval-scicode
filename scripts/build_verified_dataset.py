"""Rebuild the SciCode-Verified gzip artifact from the exact pinned git object.

Usage: python scripts/build_verified_dataset.py /path/to/scicode-verified-clone
The clone must contain commit VERIFIED_REVISION of flyingwagner/scicode-verified.
The same bytes are published as data/problems_test.jsonl in the Hugging Face dataset
shhu2001/SciCode-Verified at revision VERIFIED_HF_REVISION and in the GitHub 'data'
release; the release manifest pins their MD5.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

VERIFIED_REVISION = 'ddab4a92f8d80a7113ab946628e994b52354d838'
VERIFIED_HF_REVISION = 'eea11a866be6860725258702b39ef8651ed26abd'
SOURCE_SHA256 = '427771cb8bceb5058e8b510af0ee2c8210827a1491e0cdf8e6db56d4ed1440ba'
SOURCE_MD5 = '5c604d8dbf52642bd94e13b92c8f52eb'  # scicode_verified/manifest.json problems_test_jsonl_md5
TEST_DATA = {'file': 'test_data_cleaned.h5', 'size': 1108078257,
             'md5': '2b41a7df40ddc23ce651ec05b8ecb6f8',
             'sha256': '8fb6e575b7b6dda5e48b04dea338fc6af4fe185774b8f19221c96945df9b4142'}
# Official skip steps: no test cases upstream or in the verified release; the release
# counts them as unscored. 72.6 also has no direct tests in the release (its seeded
# exact-lattice tests were deleted; 72.7 checks it downstream), but it remains scored.
UNSCORED = ['13.6', '62.1', '76.3']
UNTESTED = ['13.6', '62.1', '72.6', '76.3']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('clone', type=Path)
    args = parser.parse_args()
    data = Path(__file__).resolve().parents[1]/'scicode/data'
    raw = subprocess.check_output(['git', '-C', str(args.clone), 'show',
                                   f'{VERIFIED_REVISION}:scicode_verified/problems_test.jsonl'])
    release = json.loads(subprocess.check_output(['git', '-C', str(args.clone), 'show',
                                                  f'{VERIFIED_REVISION}:scicode_verified/manifest.json']))
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA256
    assert hashlib.md5(raw).hexdigest() == SOURCE_MD5 == release['problems_test_jsonl_md5']
    assert release['h5_md5'] == TEST_DATA['md5'] and release['version'] == 'v2'
    records = [json.loads(line) for line in raw.splitlines()]
    ids = [r['problem_id'] for r in records]
    assert ids == release['problem_order'] and len(ids) == len(set(ids)) == release['n_problems'] == 64
    steps = [s for r in records for s in r['sub_steps']]
    assert not any(s.get('ground_truth_code') for s in steps)  # no test-set reference code is released
    assert [s['step_number'] for s in steps if not s['test_cases']] == UNTESTED
    blob = gzip.compress(raw, mtime=0)
    (data/'problems_verified_test.jsonl.gz').write_bytes(blob)
    manifest = {'dataset': 'flyingwagner/scicode-verified', 'version': release['version'],
                'revision': VERIFIED_REVISION, 'path': 'scicode_verified/problems_test.jsonl',
                'huggingface': {'dataset': 'shhu2001/SciCode-Verified', 'revision': VERIFIED_HF_REVISION},
                'derived_from': {'dataset': 'scicode-bench/SciCode', 'revision': '69a8cfc829fe8788a426ce8b5de6292366dce7ef'},
                'license': 'Apache-2.0', 'count': len(records), 'task_ids': ids,
                'source_sha256': SOURCE_SHA256, 'source_md5': SOURCE_MD5,
                'artifact_sha256': hashlib.sha256(blob).hexdigest(),
                'subproblems': len(steps), 'scored_subproblems': len(steps) - len(UNSCORED),
                'unscored_subproblems': UNSCORED, 'untested_subproblems': UNTESTED, 'test_data': TEST_DATA}
    (data/'verified_manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')


if __name__ == '__main__':
    main()
