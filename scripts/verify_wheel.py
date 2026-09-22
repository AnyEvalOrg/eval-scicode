"""Verify a wheel installed into an isolated site-packages directory, offline.

Usage: python scripts/verify_wheel.py .build/wheel-env/site-packages
Run after pip install --no-deps --target <site-packages> <wheel>. No model or sandbox
is started. Inspect recognizes installed packages by their site-packages location.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_packages", type=Path)
    args = parser.parse_args()
    installed = args.site_packages.resolve()
    root = Path(__file__).resolve().parents[1]
    empty = root / ".build/empty"
    empty.mkdir(parents=True, exist_ok=True)
    os.chdir(empty)
    sys.path = [str(installed)] + [
        p for p in sys.path
        if p and not Path(p).resolve().is_relative_to(root)
    ]

    def blocked(*args, **kwargs):
        raise AssertionError("Network disabled during wheel validation")

    socket.create_connection = blocked
    socket.socket.connect = blocked

    from inspect_ai._util.registry import registry_create

    # No prior import of scicode: this must load the installed entry point.
    task = registry_create("task", "scicode/scicode", sandbox_type="docker")
    import scicode
    from scicode.dataset import manifest

    assert Path(scicode.__file__).is_relative_to(installed)
    assert len(task.dataset) == 65
    assert [sample.id for sample in task.dataset] == manifest()["splits"]["all"]["task_ids"]
    assert task.epochs == 1
    assert Path(task.sandbox.config).is_relative_to(installed)
    extended = registry_create("task", "scicode/scicode", sandbox_type="docker", include_dev_set=True)
    assert len(extended.dataset) == 80
    from importlib.resources import files
    from importlib.metadata import version
    assert version('eval-scicode') == '1.0.0'
    for asset in ('Dockerfile', 'values.yaml', 'compose.yaml', 'chart/Chart.yaml',
                  'docker-requirements.txt', 'data/problems_all.jsonl.gz', 'data/problems_dev.jsonl.gz', 'chart/templates/pod.yaml', 'chart/templates/network-policy.yaml'):
        assert files('scicode').joinpath(asset).is_file()
    assert not files('scicode').joinpath('test_data.h5').is_file()
    default = registry_create("task", "scicode/scicode")
    assert Path(default.sandbox.config.chart).is_relative_to(installed)
    print("Cold installed-wheel discovery: PASS; 1 task x 65 test ids, 80 with dev; network blocked; no checkout imports.")


if __name__ == "__main__":
    main()
