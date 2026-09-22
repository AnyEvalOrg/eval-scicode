"""Fetch the pinned large asset before building; never include it in git/wheels."""
import argparse
import hashlib
from pathlib import Path

FILE_ID = '17G_k65N_6yFFZ2O-jQH00Lh6iaw3z-AW'
SHA256 = '48b0272a88b17dbd29777c217e1b4fb2b019b92e11cc2add847409db9541b890'


def verify(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == SHA256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('scicode/test_data.h5'))
    args = parser.parse_args()
    if args.output.exists() and verify(args.output):
        print('Pinned test data verified.')
        return
    import gdown
    temporary = args.output.with_suffix('.download')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        gdown.download(id=FILE_ID, output=str(temporary), quiet=False)
        if not verify(temporary):
            raise RuntimeError('Test data checksum mismatch')
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print('Pinned test data verified.')


if __name__ == '__main__':
    main()
