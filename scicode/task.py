"""SciCode main-problem pass@1, defaulting to the 65 held-out problems."""
import os
from importlib.resources import files
from pathlib import Path
from inspect_ai import Task, task
from .dataset import get_dataset, manifest
from .solver import solve_scicode_problem
from .scoring import verify


def task_sandbox(sandbox_type='k8s', anyeval_chart=True):
    if sandbox_type not in {'k8s', 'docker'}:
        raise ValueError('sandbox_type must be k8s or docker')
    resources = files('scicode')
    config = str(resources.joinpath('values.yaml' if sandbox_type == 'k8s' else 'compose.yaml'))
    if sandbox_type == 'k8s' and anyeval_chart:
        from k8s_sandbox import K8sSandboxEnvironmentConfig
        os.environ.setdefault('INSPECT_K8S_DEFAULT_NAMESPACE', 'anyeval-sandbox')
        config = K8sSandboxEnvironmentConfig(chart=str(resources.joinpath('chart')), values=Path(config))
    return sandbox_type, config


@task
def scicode(*, provide_scientific_background=False, timeout=300, include_dev_set=False,
            sandbox_type='k8s', anyeval_chart=True):
    if type(timeout) is not int or not 0 < timeout <= 3600:
        raise ValueError('timeout must be an integer from 1 to 3600')
    return Task(dataset=get_dataset(include_dev_set), solver=solve_scicode_problem(provide_scientific_background),
                scorer=verify(timeout), sandbox=task_sandbox(sandbox_type, anyeval_chart),
                epochs=1, version='1.0.0', metadata={'metric':'main-problem pass@1', 'dataset_provenance':manifest()})
