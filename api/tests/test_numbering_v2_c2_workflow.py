"""Keep this PR-only experiment separate from the existing deployment workflow."""
from pathlib import Path
import pytest

yaml = pytest.importorskip('yaml', reason='C2 dedicated workflow installs its exact test lock')

ROOT = Path(__file__).resolve().parents[2]


def test_c2_workflow_is_read_only_and_pr_only():
    text = (ROOT / '.github/workflows/c2-linux-tests.yml').read_text(encoding='utf8')
    data = yaml.load(text, Loader=yaml.BaseLoader)
    assert set(data['on']) == {'pull_request'}
    assert data['permissions'] == {'contents': 'read'}
    assert data['concurrency']['group'].startswith('c2-linux-pr-')
    assert set(data['jobs']) == {'c2-linux'}
    job = data['jobs']['c2-linux']
    assert job['runs-on'] == 'ubuntu-24.04'
    assert 'environment' not in job
    assert not any(word in text.lower() for word in ('secrets.', 'ssh', 'workflow_dispatch', 'push:', 'environment: production'))
    assert any(s.get('with', {}).get('python-version') == '3.12.3' for s in job['steps'])
    assert any(s.get('with', {}).get('persist-credentials') == 'false' for s in job['steps'])
