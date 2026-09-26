import json
from pathlib import Path
import shutil
import subprocess
import pytest
import yaml
from scicode.task import scicode


@pytest.mark.parametrize('release',['first','second'])
def test_helm_chart_real_render(release):
    helm=shutil.which('helm')
    assert helm, 'helm is required on PATH'
    config=scicode().sandbox.config
    result=subprocess.run([helm,'template',release,str(config.chart),'-n','anyeval-sandbox','-f',str(config.values)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    documents=[d for d in yaml.safe_load_all(result.stdout) if d]
    assert {d['kind'] for d in documents}=={'Pod','NetworkPolicy'}
    pod=next(d for d in documents if d['kind']=='Pod')
    policy=next(d for d in documents if d['kind']=='NetworkPolicy')
    assert policy['spec']['podSelector']['matchLabels']=={'app.kubernetes.io/instance':release}
    assert policy['spec']['ingress']==policy['spec']['egress']==[]
    assert set(policy['spec']['policyTypes'])=={'Ingress','Egress'}
    spec=pod['spec'];container=spec['containers'][0]
    assert spec['runtimeClassName']=='gvisor'
    assert spec['nodeSelector']['cloud.google.com/gke-spot']=='true'
    assert not spec['automountServiceAccountToken']
    assert container['resources']['requests']==container['resources']['limits']
    assert container['resources']['limits']['memory']=='6Gi'
    assert container['image']=='us-central1-docker.pkg.dev/openevalz-sbx-84737/openevalz/eval-scicode-sandbox:1.1.0'
    assert container['securityContext']['readOnlyRootFilesystem']
    assert 'SYS_PTRACE' in container['securityContext']['capabilities']['add']


def test_task_and_catalog():
    assert len(scicode(sandbox_type='docker').dataset)==65
    assert len(scicode(include_dev_set=True,sandbox_type='docker').dataset)==80
    assert scicode(sandbox_type='docker').config.temperature is None
    catalog=json.loads(Path('anyeval.json').read_text())
    assert catalog['tasks'][0]=={'name':'scicode','samples':65}
    assert catalog['total_samples']==65+64  # scicode_verified: tests/test_verified.py


def test_docker_target_security_and_build_paths():
    text=Path('scicode/Dockerfile').read_text()
    assert text.startswith('FROM python:3.12-slim-trixie')
    assert 'chmod 0400 /opt/scicode/test_data.h5' in text
    assert 'chown root:root' in text and 'sha256sum -c -' in text
    compose=yaml.safe_load(Path('scicode/compose.yaml').read_text())['services']['default']
    assert compose['mem_limit']=='6g'
    assert compose['network_mode']=='none' and compose['read_only']
    assert 'eval-scicode-sandbox:1.1.0' in compose['image']
    assert 'build' not in compose  # installed wheels do not ship the large asset


def test_provider_chart_preserves_security_and_release_policy():
    import k8s_sandbox
    chart=Path(k8s_sandbox.__file__).parent/'resources/helm/agent-env'
    values=scicode(anyeval_chart=False).sandbox.config
    result=subprocess.run(['helm','template','provider-check',str(chart),'-f',str(values)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    docs=[x for x in yaml.safe_load_all(result.stdout) if x]
    workload=next(x for x in docs if x['kind']=='StatefulSet')['spec']['template']['spec']
    service=yaml.safe_load(Path(values).read_text())['services']['default']
    container=next(c for c in workload['containers'] if c['name']=='default')
    assert container['securityContext']==service['securityContext']
    assert container['resources']['requests']==container['resources']['limits']
    assert container['resources']['limits']['memory']=='6Gi'
    assert workload['runtimeClassName']=='gvisor'
    policy=next(x for x in docs if x['kind']=='NetworkPolicy')
    assert policy['spec']['podSelector']['matchLabels']['app.kubernetes.io/instance']=='provider-check'
