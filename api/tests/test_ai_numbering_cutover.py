"""Generated business + mirror + vectors only; never use real images/services."""
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
import io
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
from unittest.mock import Mock

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from passlib.hash import apr_md5_crypt
import pytest

from app.numbering_maintenance import Maintenance, MaintenanceError
from app.numbering_acceptance import create_acceptance_app, issue_capability
from app.services import product_code_cutover as business
from app.services.numbering_environment import file_binding
from app.services.visual_search import numbering_cutover as tool
from app.services.visual_search.catalog_metadata import SOURCE_ID, catalog_context
from app.services.visual_search.config import VisualConfig
from app.services.visual_search.factory import production_service
from app.services.visual_search.production_catalog import sync_catalog
from app.services.visual_search.images import VisualError
from app.services.visual_search.runtime_control import RuntimeSwitch
from .test_product_code_cutover import SyntheticEnvironment
from .test_product_code_offline import synthetic_backup, database
from .test_production_catalog import Backend
from .test_visual_search import MockProvider

OPERATION = 'synthetic-ai-cutover-001'


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    original = socket.socket.connect
    def forbidden(sock, address):
        if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return original(sock, address)  # asyncio's private Windows socketpair.
        raise AssertionError('No network or real AI is allowed in cutover tests')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr('app.services.visual_search.production_catalog.time.sleep', lambda _: None)
    monkeypatch.setattr('app.services.visual_search.service.time.sleep', lambda _: None)


@pytest.fixture
def stage(tmp_path, request):
    backend = Backend()
    backend.products[0]['original_product_code'] = '000-ORIGINAL'
    path, _ = synthetic_backup(tmp_path / 'business')
    with database(path) as db:
        db.executescript('DELETE FROM inventory_movements; DELETE FROM product_packagings; DELETE FROM products;')
        for pid in range(1, 5):
            code = backend.products[pid - 1]['product_code'] if pid <= len(backend.products) else 'UNMIRRORED'
            db.execute('INSERT INTO products(id,product_code,original_product_code,category_id,warehouse_id,is_active,image_path,thumbnail_path,size,unit) '
                       'VALUES(?,?,?,1,1,1,\'uploads/synthetic.jpg\',NULL,\'合成\',\'pcs\')', (pid, code, f'000-{pid}'))
            db.execute('INSERT INTO product_packagings(product_id,packing_qty,carton_count,sort_order) VALUES(?,24,?,0)', (pid, pid))
    cfg = Config()
    cfg.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'alembic'))
    cfg.set_main_option('sqlalchemy.url', f'sqlite:///{path.as_posix()}')
    command.upgrade(cfg, business.REVISION)
    context = SyntheticEnvironment(path.parent, path)
    business.enter(context, OPERATION, 'b' * 40)
    backup = tmp_path / 'backup'
    backup.mkdir()
    source = backup / 'inventory.db'
    shutil.copyfile(path, source)
    archive = backup / 'uploads.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        entry = tarfile.TarInfo('uploads/synthetic.jpg')
        entry.size = 5
        bundle.addfile(entry, io.BytesIO(b'image'))
    manifest = backup / 'manifest.json'
    manifest.write_text(json.dumps({'backup_version': 1, 'database_filename': source.name,
        'database_sha256': file_binding(source)['sha256'], 'database_size': source.stat().st_size,
        'uploads_archive': archive.name, 'uploads_sha256': file_binding(archive)['sha256'],
        'uploads_archive_size': archive.stat().st_size, 'uploads_file_count': 1}), encoding='utf8')
    before = business.plan(context, OPERATION, source, manifest)
    completed = business.plan(context, OPERATION, source, manifest, execute=True,
                              confirm_production_numbering=True, expected_plan_sha256=before['plan_sha256'])
    reports = Path(completed['reports_directory'])
    visual = tmp_path / 'visual'
    catalog = visual / 'production-catalog'
    selected_ids = getattr(request, 'param', {}).get('mirror_ids')
    if selected_ids is not None:
        backend.products = [p for p in backend.products if p['id'] in selected_ids]
    with closing(backend.client()) as client:
        sync_catalog(client, catalog, SOURCE_ID, 'SYNTHETIC ONLY', execute=True)
    config = VisualConfig(dimension=256, api_key='mock-only', interval=.1, daily_budget_cny=Decimal('1'),
                          call_reserve_cny=Decimal('.003'))
    provider = MockProvider()
    service = production_service(visual, config, provider=provider, initialize=True)
    provider.record = service.store.record_call
    service.rebuild(None, 'synthetic', 10, False, True, background=False)
    reservation = service.budget.reserve()
    service.budget.settle(reservation, {'input_tokens': 4, 'image_tokens': 20}, known=True)
    RuntimeSwitch(visual).set_enabled(False)
    sync_id = service.source.mirror.metadata()['last_report']['sync_id']
    inputs = tool.Inputs(visual, catalog, reports / 'report.json', reports / 'mapping.csv',
        catalog / 'manifests' / (sync_id + '.json'),
        tmp_path / 'private-reports', OPERATION, completed['completion_sha256'],
        file_binding(reports / 'mapping.csv')['sha256'], service.source.identity_key, service.config.index_key)
    # Keep every counter for the operation below separate from fixture indexing.
    provider.calls = 0
    yield {'inputs': inputs, 'business': path, 'context': context, 'service': service, 'provider': provider,
           'completed': completed, 'backend': backend, 'root': tmp_path}
    service.close()


def execute(stage, plan=None):
    plan = plan or tool.plan(stage['inputs'])
    return tool.plan(stage['inputs'], execute=True, confirm_metadata_only=True, expected_plan_sha256=plan['plan_sha256'])


def test_metadata_only_success_and_zero_cost_invariants(stage, monkeypatch):
    inputs, service = stage['inputs'], stage['service']
    initial = {name: Path(value['path']).read_bytes() for name, value in tool.protected_files(inputs)[1].items()}
    old = service.source.mirror.products()
    image_files = {str(p): file_binding(p) for p in (inputs.catalog_root / 'images').rglob('*') if p.is_file()}
    vectors = tool.vector_evidence(inputs)
    budget = service.budget.snapshot()
    def blocked(*a, **kw):
        raise AssertionError('Must not embed, download, preprocess, synchronize or create a job')
    spies = []
    for module, name in [(service.provider, 'embed'), ('app.services.visual_search.service', 'prepare_image'),
                         ('app.services.visual_search.production_catalog', 'download_image'),
                         ('app.services.visual_search.production_catalog', 'sync_catalog'),
                         ('app.services.visual_search.service.VisualService', 'rebuild')]:
        spy = Mock(side_effect=blocked)
        monkeypatch.setattr(module + '.' + name, spy) if isinstance(module, str) else monkeypatch.setattr(module, name, spy)
        spies.append(spy)
    plan = tool.plan(inputs)
    assert plan['business_products'] == 4 and plan['mirror_products'] == 3
    assert plan['missing_from_mirror'] == [4]
    result = execute(stage, plan)
    assert result['state'] == 'committed'
    assert [r['product_code'] for r in service.source.mirror.products()] == ['250001', '250002', '250003']
    for before, after in zip(old, service.source.mirror.products()):
        assert {k: v for k, v in before.items() if k != 'product_code'} == {k: v for k, v in after.items() if k != 'product_code'}
    assert service.source.mirror.products()[0]['original_product_code'] == '000-ORIGINAL'
    assert service.source.identity_key == inputs.namespace and service.config.index_key == inputs.index_key
    assert tool.status(inputs)['commit_sha256'] == result['commit_sha256']
    assert service.source.mirror.products()[-1]['id'] == 3  # No auto addition.
    assert all(spy.call_count == 0 for spy in spies) and stage['provider'].calls == 0
    assert tool.vector_evidence(inputs) == vectors == result['vector_evidence']
    assert service.budget.snapshot() == budget
    assert {str(p): file_binding(p) for p in (inputs.catalog_root / 'images').rglob('*') if p.is_file()} == image_files
    assert {name: Path(value['path']).read_bytes() for name, value in tool.protected_files(inputs)[1].items()} == initial
    assert catalog_context(inputs.catalog_root)['catalog_fingerprint'] != plan['fingerprint_before']


def test_default_plan_does_not_write_files(stage):
    root = stage['inputs'].visual_root
    before = {str(p): file_binding(p) for p in root.rglob('*') if p.is_file()}
    assert tool.plan(stage['inputs'])['state'] == 'planned'
    assert {str(p): file_binding(p) for p in root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('stage', [{'mirror_ids': [1, 3]}], indirect=True)
def test_subset_uses_complete_business_rank_without_filling_gaps(stage):
    result = execute(stage)
    assert result['mirror_products'] == 2 and result['business_products'] == 4
    assert [r['product_code'] for r in stage['service'].source.mirror.products()] == ['250001', '250003']
    assert result['missing_from_mirror'] == [2, 4]


def test_feedback_and_metadata_cutover_share_the_exact_os_lock(stage):
    from .test_production_catalog import photo
    service = stage['service']
    result = service.search(None, 'admin', photo('red'))
    before = service.store.path.read_bytes()
    with tool.paused(stage['inputs']):
        with pytest.raises(VisualError, match='正忙'):
            service.feedback(None, 'admin', result['query_id'], 1, False)
    assert service.store.path.read_bytes() == before
    assert service.feedback(None, 'admin', result['query_id'], 1, False)['saved'] is True


@pytest.mark.parametrize('change', ['operation_id', 'completion_sha256', 'mapping_sha256', 'namespace', 'index_key'])
def test_wrong_approval_or_identity_refused(stage, change):
    inputs = replace(stage['inputs'], **{change: '0' * 64})
    with pytest.raises((MaintenanceError, ValueError)):
        tool.plan(inputs)


def test_partial_mapping_and_manifest_drift_refused(stage):
    inputs = stage['inputs']
    inputs.mapping.write_text('product_id,old_code,new_code\n1,MOCK-1,250001\n', encoding='utf8')
    with pytest.raises(MaintenanceError):
        tool.plan(inputs)


def test_unknown_mirror_product_refused(stage):
    inputs = stage['inputs']
    with database(inputs.catalog_root / 'catalog.sqlite') as db:
        row = json.loads(db.execute('SELECT document FROM mirror_products LIMIT 1').fetchone()[0])
        row.update(id=999, product_key=SOURCE_ID + ':999')
        db.execute('INSERT INTO mirror_products VALUES(?,?,?)', (SOURCE_ID, 999, json.dumps(row)))
    with pytest.raises(MaintenanceError, match='未知商品'):
        tool.plan(inputs)


def test_unconfirmed_or_stale_execute_refused(stage):
    with pytest.raises(MaintenanceError, match='独立确认'):
        tool.plan(stage['inputs'], execute=True)
    with pytest.raises(MaintenanceError, match='独立确认'):
        tool.plan(stage['inputs'], execute=True, confirm_metadata_only=True, expected_plan_sha256='0' * 64)


def test_rollback_interrupt_and_repeat_protection(stage, monkeypatch):
    inputs = stage['inputs']
    before = (inputs.catalog_root / 'catalog.sqlite').read_bytes()
    verify = tool.verify_committed
    def fail(*a):
        raise KeyboardInterrupt
    monkeypatch.setattr(tool, 'verify_committed', fail)
    with pytest.raises(KeyboardInterrupt):
        execute(stage)
    assert (inputs.catalog_root / 'catalog.sqlite').read_bytes() == before
    assert tool.status(inputs)['state'] == 'not_committed'
    monkeypatch.setattr(tool, 'verify_committed', verify)
    assert execute(stage)['state'] == 'committed'
    with pytest.raises(MaintenanceError, match='禁止重复'):
        execute(stage, {'plan_sha256': '0' * 64})


def test_committed_report_failure_and_readonly_recovery(stage, monkeypatch):
    original, count = tool.export, 0
    def fail_after_commit(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('synthetic full disk')
        return original(*args)
    monkeypatch.setattr(tool, 'export', fail_after_commit)
    result = execute(stage)
    assert result['state'] == 'committed_report_failed'
    before = (stage['inputs'].catalog_root / 'catalog.sqlite').read_bytes()
    assert tool.status(stage['inputs'], recover_report=True)['state'] == 'committed'
    assert (stage['inputs'].catalog_root / 'catalog.sqlite').read_bytes() == before


def test_enabled_ai_or_inflight_job_refused(stage):
    inputs = stage['inputs']
    RuntimeSwitch(inputs.visual_root).set_enabled(True)
    with pytest.raises(MaintenanceError, match='关闭 AI'):
        tool.plan(inputs)


def test_running_index_job_refused(stage):
    with stage['service'].store.connection() as db:
        db.execute("UPDATE visual_jobs SET status='running',finished_at=NULL")
    with pytest.raises(MaintenanceError, match='未排空'):
        tool.plan(stage['inputs'])


def test_wrong_manifest_old_code_and_after_plan_drift_refused(stage):
    inputs = stage['inputs']
    plan = tool.plan(inputs)
    with database(inputs.catalog_root / 'catalog.sqlite') as db:
        row = json.loads(db.execute('SELECT document FROM mirror_products WHERE id=1').fetchone()[0])
        row['product_code'] = 'UNAPPROVED-OLD'
        db.execute('UPDATE mirror_products SET document=? WHERE id=1', (json.dumps(row),))
    with pytest.raises(MaintenanceError, match='漂移'):
        execute(stage, plan)


@pytest.mark.parametrize('selected', [None, 1])
@pytest.mark.parametrize('legacy', [False, True])
def test_old_feedback_rejected_even_for_no_match(stage, selected, legacy):
    from .test_production_catalog import photo
    service = stage['service']
    result = service.search(None, 'admin', photo('red'))
    if legacy:
        with service.store.connection() as db:
            db.execute('UPDATE visual_queries SET candidate_ids=? WHERE id=?', (json.dumps([1, 2, 3]), result['query_id']))
    execute(stage)
    before = service.store.path.read_bytes()
    with pytest.raises(VisualError, match='目录已更新') as error:
        service.feedback(None, 'admin', result['query_id'], selected, selected is None)
    assert error.value.status_code == 409
    assert service.store.path.read_bytes() == before


def test_new_query_feedback_keeps_top5_and_uses_new_number(stage):
    from .test_production_catalog import photo
    execute(stage)
    service = stage['service']
    result = service.search(None, 'admin', photo('red'))
    assert 1 <= len(result['candidates']) <= 5
    chosen = result['candidates'][0]['product']
    assert chosen['product_code'] == f"25{chosen['id']:04d}"
    assert service.feedback(None, 'admin', result['query_id'], chosen['id'], False)['saved'] is True


def test_version_endpoint_is_cold_and_free_even_without_model_key(stage, monkeypatch):
    from app.main import create_app
    from app.database import get_db
    auth = stage['root'] / 'version.htpasswd'
    auth.write_text('user:' + apr_md5_crypt.hash('mock'), encoding='utf8')
    app = create_app(data_directory=stage['root'] / 'cold', production_catalog_directory=stage['inputs'].catalog_root,
                     visual_config=VisualConfig(htpasswd_file=str(auth), admin_users=()))
    def forbidden(*args, **kwargs):
        raise AssertionError('Version GET must not initialize a service or touch budget/business DB')
    monkeypatch.setattr(app.state.visual_sources, 'get', forbidden)
    monkeypatch.setattr('app.services.visual_search.runtime_control.DailyBudgetLedger._connect', forbidden)
    app.dependency_overrides[get_db] = forbidden
    with TestClient(app) as client:
        response = client.get('/api/visual-search/catalog/version?source=production-catalog', auth=('user', 'mock'))
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'private, no-store'
        old = response.json()
        execute(stage)
        current = client.get('/api/visual-search/catalog/version?source=production-catalog', auth=('user', 'mock')).json()
        assert old['catalog_fingerprint'] != current['catalog_fingerprint']
        assert old['index_namespace'] == current['index_namespace']
    assert stage['provider'].calls == 0


def test_main_startup_during_maintenance_does_not_initialize_ai(stage, monkeypatch):
    from app.main import create_app
    def forbidden(*a, **kw):
        raise AssertionError('Held application must not initialize AI')
    initializer = Mock(side_effect=forbidden)
    monkeypatch.setattr('app.main.VisualService', initializer)
    monkeypatch.setattr('app.services.visual_search.runtime_control.DailyBudgetLedger._connect', forbidden)
    app = create_app(data_directory=stage['business'].parent)
    assert initializer.call_count == 0
    with TestClient(app) as client:
        assert client.post('/api/products').status_code == 503
        assert client.get('/api/visual-search/catalog/version').status_code == 503
    assert Maintenance(stage['business'].parent).held()


def test_process_death_can_restore_hot_journal_without_reexecuting(stage):
    inputs = stage['inputs']
    original = tool.plan(inputs)
    code = "import os,sqlite3,sys; d=sqlite3.connect(sys.argv[1]); d.execute('PRAGMA cache_size=1'); d.execute('BEGIN IMMEDIATE'); d.execute('UPDATE mirror_products SET document=?',('x'*200000,)); os._exit(19)"
    assert subprocess.run([sys.executable, '-c', code, str(inputs.catalog_root / 'catalog.sqlite')]).returncode == 19
    with pytest.raises(MaintenanceError, match='sidecar'):
        tool.plan(inputs)
    assert tool.status(inputs, recover_transaction=True)['state'] == 'not_committed'
    assert tool.plan(inputs)['plan_sha256'] == original['plan_sha256']


@pytest.fixture
def acceptance(stage):
    execute(stage)
    root = stage['root']
    auth = root / 'synthetic.htpasswd'
    auth.write_text('admin:' + apr_md5_crypt.hash('mock') + '\nordinary:' + apr_md5_crypt.hash('mock'), encoding='utf8')
    token = root / 'private-token.json'
    common = dict(database=stage['business'], catalog_root=stage['inputs'].catalog_root)
    issue_capability(token, OPERATION, stage['completed']['completion_sha256'], **common)
    def create():
        return create_acceptance_app(token, OPERATION, stage['completed']['completion_sha256'],
                                     htpasswd_file=auth, admin_users=('admin',), **common)
    return stage, token, create


def test_controlled_acceptance_reads_only_with_admin_and_private_capability(acceptance):
    stage, token, create = acceptance
    headers = {'X-Numbering-Acceptance': json.loads(token.read_text())['token']}
    before = stage['business'].read_bytes()
    with TestClient(create(), client=('127.0.0.1', 12345)) as client:
        assert client.get('/acceptance/products', auth=('admin', 'mock')).status_code == 403
        assert client.get('/acceptance/products', auth=('ordinary', 'mock'), headers=headers).status_code == 403
        for path in ('/acceptance/products', '/acceptance/products/1', '/acceptance/stock',
                     '/acceptance/catalog', '/acceptance/catalog/products', '/acceptance/health'):
            response = client.get(path, auth=('admin', 'mock'), headers=headers)
            assert response.status_code == 200, response.text
            assert response.headers['cache-control'] == 'private, no-store'
        for path in ('/acceptance/products', '/api/products', '/api/visual-search/search', '/api/product-images'):
            assert client.post(path, auth=('admin', 'mock'), headers=headers).status_code == 503
        assert client.get('/api/visual-search/search', auth=('admin', 'mock'), headers=headers).status_code == 404
    assert stage['business'].read_bytes() == before
    assert Maintenance(stage['business'].parent).held()
    with TestClient(create(), client=('127.0.0.1', 12345)) as restarted:
        assert restarted.get('/acceptance/health', auth=('admin', 'mock'), headers=headers).status_code == 200
    with TestClient(create(), client=('192.0.2.10', 12345)) as external:
        assert external.get('/acceptance/products', auth=('admin', 'mock'), headers=headers).status_code == 403


def test_acceptance_token_expiry_revocation_and_no_initialization(acceptance, monkeypatch):
    stage, token, create = acceptance
    def forbidden(*a, **kw):
        raise AssertionError('Read acceptance must never initialize AI or run an index')
    monkeypatch.setattr('app.services.visual_search.service.VisualService.__init__', forbidden)
    monkeypatch.setattr('app.services.visual_search.runtime_control.DailyBudgetLedger._connect', forbidden)
    headers = {'X-Numbering-Acceptance': json.loads(token.read_text())['token']}
    with TestClient(create(), client=('127.0.0.1', 12345)) as client:
        assert client.get('/acceptance/health', auth=('admin', 'mock'), headers=headers).status_code == 200
        data = json.loads(token.read_text())
        data['expires_at'] = 1
        token.write_text(json.dumps(data))
        assert client.get('/acceptance/health', auth=('admin', 'mock'), headers=headers).status_code == 503
    assert Maintenance(stage['business'].parent).held()


def test_reader_scopes_work_on_separate_hosts_without_copying_business_database(acceptance, monkeypatch):
    stage, token, _ = acceptance
    ai_token = stage['root'] / 'ai-only-token.json'
    issue_capability(ai_token, OPERATION, stage['completed']['completion_sha256'], catalog_root=stage['inputs'].catalog_root)
    def forbidden(*a, **kw):
        raise AssertionError('Catalog-only reader must not open a business database')
    monkeypatch.setattr(business, 'connect', forbidden)
    app = create_acceptance_app(ai_token, OPERATION, stage['completed']['completion_sha256'],
        htpasswd_file=stage['root'] / 'synthetic.htpasswd', admin_users=('admin',), catalog_root=stage['inputs'].catalog_root)
    with TestClient(app, client=('127.0.0.1', 12345)) as client:
        headers = {'X-Numbering-Acceptance': json.loads(ai_token.read_text())['token']}
        assert client.get('/acceptance/catalog', auth=('admin', 'mock'), headers=headers).status_code == 200
        assert client.get('/acceptance/products', auth=('admin', 'mock'), headers=headers).status_code == 404


def test_acceptance_refuses_wrong_operation_and_completion(acceptance):
    stage, _, _ = acceptance
    for operation, completion in [('wrong-operation', stage['completed']['completion_sha256']), (OPERATION, '0' * 64)]:
        with pytest.raises(MaintenanceError):
            issue_capability(stage['root'] / 'should-not-exist.json', operation, completion,
                             database=stage['business'], catalog_root=stage['inputs'].catalog_root)
        assert not (stage['root'] / 'should-not-exist.json').exists()
