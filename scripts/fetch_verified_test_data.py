"""Fetch the pinned SciCode-Verified targets before building; never include them in git/wheels.

Source: test_data_cleaned.h5 in the Hugging Face dataset shhu2001/SciCode-Verified at a
pinned revision (mirrored byte-identically in the flyingwagner/scicode-verified GitHub
'data' release). The release manifest pins MD5 2b41a7df40ddc23ce651ec05b8ecb6f8; the
build pins the SHA-256 below.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import urllib.request

HF_REVISION = 'eea11a866be6860725258702b39ef8651ed26abd'
URL = ('https://huggingface.co/datasets/shhu2001/SciCode-Verified/resolve/'
       f'{HF_REVISION}/test_data_cleaned.h5')
SHA256 = '8fb6e575b7b6dda5e48b04dea338fc6af4fe185774b8f19221c96945df9b4142'
MD5 = '2b41a7df40ddc23ce651ec05b8ecb6f8'


def verify(path):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            sha.update(block)
            md5.update(block)
    return sha.hexdigest() == SHA256 and md5.hexdigest() == MD5


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('scicode/test_data_cleaned.h5'))
    args = parser.parse_args()
    if args.output.exists() and verify(args.output):
        print('Pinned SciCode-Verified test data verified.')
        return
    temporary = args.output.with_suffix('.download')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(URL) as response, temporary.open('wb') as stream:
            shutil.copyfileobj(response, stream, 1 << 20)
        if not verify(temporary):
            raise RuntimeError('Test data checksum mismatch')
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print('Pinned SciCode-Verified test data verified.')


if __name__ == '__main__':
    main()
