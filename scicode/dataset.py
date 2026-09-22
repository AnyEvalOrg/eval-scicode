"""Offline integrity-checked private source records, never attached to Samples."""
import gzip
import hashlib
import json
from importlib.resources import files

SCICODE_GITHUB_REVISION = '69a8cfc829fe8788a426ce8b5de6292366dce7ef'
SCICODE_TEST_GDRIVE_ID = '17G_k65N_6yFFZ2O-jQH00Lh6iaw3z-AW'
TEST_DATA_SHA256 = '48b0272a88b17dbd29777c217e1b4fb2b019b92e11cc2add847409db9541b890'
CHECKSUMS = {'all':'38797fef78f434720be6d053b4f3a86839d6f8ea5fb9115450677cd3a6edf81d',
             'dev':'193968aff23b7ed931c8f6d196b611e2f6694adb49e6cc085444a60ad2fc4f7b'}


def manifest():
    return json.loads(files('scicode').joinpath('data/manifest.json').read_text())


def load_records(include_dev_set=False, *, dev_only=False):
    try:
        info = manifest()
        assert info['revision'] == SCICODE_GITHUB_REVISION
        records = []
        for split in (['dev'] if dev_only else ['all', 'dev'] if include_dev_set else ['all']):
            blob = files('scicode').joinpath(f'data/problems_{split}.jsonl.gz').read_bytes()
            assert hashlib.sha256(blob).hexdigest() == info['splits'][split]['artifact_sha256']
            raw = gzip.decompress(blob)
            assert hashlib.sha256(raw).hexdigest() == CHECKSUMS[split]
            rows = [json.loads(line) for line in raw.splitlines()]
            ids = [r['problem_id'] for r in rows]
            assert ids == info['splits'][split]['task_ids']
            assert len(ids) == len(set(ids)) == (65 if split == 'all' else 15)
            records.extend(rows)
        return records
    except Exception:
        pass
    raise RuntimeError('Packaged dataset invalid; details withheld.') from None


def record_to_sample(record):
    from inspect_ai.dataset import Sample
    return Sample(id=record['problem_id'], input=record['problem_description_main'])


def get_dataset(include_dev_set=False):
    from inspect_ai.dataset import MemoryDataset
    return MemoryDataset(name='SciCode', samples=[record_to_sample(r) for r in load_records(include_dev_set)])
