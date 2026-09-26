"""SciCode main-problem pass@1: the 65 held-out problems, or the 64 SciCode-Verified ones."""
import os
from importlib.resources import files
from pathlib import Path
from inspect_ai import Task, task
from .dataset import get_dataset, get_verified_dataset, manifest, verified_manifest
from .solver import solve_scicode_problem
from .scoring import verify

# Each variant has its own sandbox image: the image bakes that variant's HDF5 targets.
SANDBOX_CONFIGS = {'scicode': ('values.yaml', 'compose.yaml'),
                   'verified': ('values-verified.yaml', 'compose-verified.yaml')}


def task_sandbox(sandbox_type='k8s', anyeval_chart=True, variant='scicode'):
    if sandbox_type not in {'k8s', 'docker'}:
        raise ValueError('sandbox_type must be k8s or docker')
    resources = files('scicode')
    values, compose = SANDBOX_CONFIGS[variant]
    config = str(resources.joinpath(values if sandbox_type == 'k8s' else compose))
    if sandbox_type == 'k8s' and anyeval_chart:
        from k8s_sandbox import K8sSandboxEnvironmentConfig
        os.environ.setdefault('INSPECT_K8S_DEFAULT_NAMESPACE', 'anyeval-sandbox')
        config = K8sSandboxEnvironmentConfig(chart=str(resources.joinpath('chart')), values=Path(config))
    return sandbox_type, config


def check_timeout(timeout):
    if type(timeout) is not int or not 0 < timeout <= 3600:
        raise ValueError('timeout must be an integer from 1 to 3600')


@task
def scicode(*, provide_scientific_background=False, timeout=300, include_dev_set=False,
            sandbox_type='k8s', anyeval_chart=True):
    check_timeout(timeout)
    return Task(dataset=get_dataset(include_dev_set), solver=solve_scicode_problem(provide_scientific_background),
                scorer=verify(timeout), sandbox=task_sandbox(sandbox_type, anyeval_chart),
                epochs=1, version='1.0.0', metadata={'metric':'main-problem pass@1', 'dataset_provenance':manifest()})


@task
def scicode_verified(*, provide_scientific_background=False, timeout=300,
                     sandbox_type='k8s', anyeval_chart=True):
    """SciCode-Verified v2: same protocol, solver and scorer; corrected problems and targets."""
    check_timeout(timeout)
    return Task(dataset=get_verified_dataset(),
                solver=solve_scicode_problem(provide_scientific_background, variant='verified'),
                scorer=verify(timeout, variant='verified'),
                sandbox=task_sandbox(sandbox_type, anyeval_chart, variant='verified'),
                epochs=1, version='1.0.0',
                metadata={'metric':'main-problem pass@1', 'dataset_provenance':verified_manifest()})
