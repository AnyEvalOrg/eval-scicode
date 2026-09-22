"""Rebuild gzip artifacts from the exact pinned SciCode git objects."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

REVISION='69a8cfc829fe8788a426ce8b5de6292366dce7ef'
CHECKSUMS={'all':'38797fef78f434720be6d053b4f3a86839d6f8ea5fb9115450677cd3a6edf81d',
           'dev':'193968aff23b7ed931c8f6d196b611e2f6694adb49e6cc085444a60ad2fc4f7b'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('clone',type=Path)
    args=parser.parse_args()
    data=Path(__file__).resolve().parents[1]/'scicode/data'
    manifest={'dataset':'scicode-bench/SciCode','revision':REVISION,'license':'Apache-2.0','splits':{}}
    for split,count in [('all',65),('dev',15)]:
        raw=subprocess.check_output(['git','-C',str(args.clone),'show',f'{REVISION}:eval/data/problems_{split}.jsonl'])
        assert hashlib.sha256(raw).hexdigest()==CHECKSUMS[split]
        records=[json.loads(line) for line in raw.splitlines()]
        assert len(records)==count
        blob=gzip.compress(raw,mtime=0)
        (data/f'problems_{split}.jsonl.gz').write_bytes(blob)
        manifest['splits'][split]={'count':count,'task_ids':[r['problem_id'] for r in records],
                                  'source_sha256':CHECKSUMS[split],'artifact_sha256':hashlib.sha256(blob).hexdigest(),
                                  'subproblems':sum(len(r['sub_steps']) for r in records)}
    (data/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


if __name__=='__main__':main()
