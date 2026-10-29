"""Synthetic files only. No HTTP, paid provider, image download or production data."""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Product, ProductPackaging, Warehouse, InventoryMovement, ProductImportBatch
from app.services import numbering_v2 as tool, numbering_catalog
from app.services.numbering_io import binding, connect, digest
from app.services.visual_search.catalog_metadata import catalog_context, fingerprint, SOURCE_ID, MAGIC
from app.services.visual_search.config import VisualConfig, PREPROCESS_VERSION
from app.services.visual_search.runtime_control import DailyBudgetLedger
from .test_visual_search import migrate, MockProvider


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    database = data / 'inventory.db'
    api = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option('script_location', str(api / 'alembic'))
    config.set_main_option('sqlalchemy.url', 'sqlite:///' + database.as_posix())
    command.upgrade(config, 'head')
    engine = create_engine('sqlite:///' + database.as_posix())
    with Session(engine) as db:
        db.add_all([Warehouse(id=1, name='合成仓库'), Warehouse(id=2, name='second synthetic warehouse')])
        db.flush()
        for i, code in zip((4, 18, 29, 90), ('250002', '250001', None, 'BROKEN')):
            p = Product(id=i, product_code=code, original_product_code='原-' + str(i),
                        warehouse_id=2 if i==18 else 1, is_active=i != 90, image_path=f'products/main/{i}.webp')
            p.packagings = [ProductPackaging(packing_qty=None if i==29 else 24, carton_count=0 if i==90 else 7)]
            if i==4:
                p.packagings.append(ProductPackaging(packing_qty=48, carton_count=2))
            db.add(p)
        db.flush()
        db.add(InventoryMovement(product_id=4, product_packaging_id=db.get(Product,4).packagings[0].id,
                                 warehouse_id=1, movement_type='IN', quantity=7, before_carton_count=0,
                                 after_carton_count=7, packing_qty_snapshot=24))
        db.add(ProductImportBatch(file_name='合成.xlsx', file_hash='a'*64, source_row_count=4, product_count=4))
        db.commit()
    engine.dispose()
    root = data / 'visual-search'
    cat = root / 'production-catalog'
    cat.mkdir(parents=True)
    (root / 'calls.lock').write_bytes(b'0')
    (cat / 'calls.lock').write_bytes(b'0')
    identity = {'format': MAGIC, 'source_id': SOURCE_ID, 'source_label': 'SYNTHETIC ONLY'}
    (cat / 'source.json').write_text(json.dumps(identity), encoding='utf8')
    projections = []
    records = []
    for i, code in ((4,'250002'),(18,'250001'),(90,'BROKEN')):
        row = {'id':i, 'product_code':code, 'original_product_code':'原-'+str(i), 'is_active':i!=90,
               'category_id':None, 'warehouse_id':1, 'size':None, 'unit':None,
               'image_identifier':f'products/main/{i}.webp', 'thumbnail_identifier':None,
               'source_updated_at':'2026-10-20', 'packagings':[]}
        projections.append(deepcopy(row))
        records.append({**row, 'source_id':SOURCE_ID, 'product_key':f'{SOURCE_ID}:{i}',
                        'source_present':True, 'synced_at':'2026-10-20', 'image_status':'ready',
                        'image':{'sha256':'c'*64, 'path':'images/synthetic.webp'}, 'image_error':None})
    original = {'products':projections, 'categories':[], 'warehouses':[{'id':1,'name':'合成仓库'}], 'source_total':3}
    with sqlite3.connect(cat / 'catalog.sqlite') as db:
        db.execute('CREATE TABLE mirror_products(source_id TEXT NOT NULL,id INTEGER NOT NULL,document TEXT NOT NULL,PRIMARY KEY(source_id,id))')
        db.execute('CREATE TABLE mirror_meta(key TEXT PRIMARY KEY,document TEXT NOT NULL)')
        for row in records:
            db.execute('INSERT INTO mirror_products VALUES(?,?,?)',(SOURCE_ID,row['id'],json.dumps(row)))
        for key, value in {'identity':identity,'last_report':{'scope':'full','catalog_fingerprint':fingerprint(original)},
                           'categories':original['categories'],'warehouses':original['warehouses']}.items():
            db.execute('INSERT INTO mirror_meta VALUES (?,?)',(key,json.dumps(value)))
    namespace = 'production-' + fingerprint(identity)
    index = root / 'production-indices' / namespace
    index.mkdir(parents=True)
    (index / 'source-binding.json').write_text(json.dumps({'format':'production-visual-index-v1',
        'source_id':SOURCE_ID,'source_identity':identity,'index_namespace':namespace}),encoding='utf8')
    migrate(index / 'index.db')
    visual_config = VisualConfig(source_identity=namespace, daily_budget_cny='1',call_reserve_cny='.003')
    key = visual_config.index_key
    with sqlite3.connect(index / 'index.db') as db:
        db.execute('INSERT INTO visual_source_context VALUES(?,?,?,?,?,?,?,?,?)',
                   (key,SOURCE_ID,namespace,fingerprint(original),visual_config.model,visual_config.dimension,
                    visual_config.version,PREPROCESS_VERSION,'before'))
        db.execute('INSERT INTO visual_image_vectors VALUES(?,?,?,?)',(key,'c'*64,b'UNCHANGED-VECTOR','before'))
        db.execute('INSERT INTO visual_embeddings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (4,key,'images/synthetic.webp','c'*64,visual_config.model,visual_config.dimension,
                    visual_config.version,PREPROCESS_VERSION,b'UNCHANGED-PRODUCT-VECTOR','before','ready',None))
    ledger=DailyBudgetLedger(root/'daily-budget.sqlite3', visual_config)
    reservation=ledger.reserve()
    ledger.settle(reservation,{},known=False)
    image=cat/'images'/'synthetic.webp'
    image.parent.mkdir()
    image.write_bytes(b'SYNTHETIC IMAGE NOT READ BY CUTOVER')
    backups=tmp_path/'backups'
    backups.mkdir()
    shutil.copy2(database,backups/'business.db')
    shutil.copy2(cat/'catalog.sqlite',backups/'catalog.db')
    options={'database':str(database),'backup_database':str(backups/'business.db'),
             'backup_catalog':str(backups/'catalog.db'),'visual_root':str(root),'catalog_root':str(cat),
             'namespace':namespace,'index_key':key,'offline_test':True}
    # Fail on every forbidden action, and separately count them.
    probes={name:Mock(side_effect=AssertionError('forbidden '+name)) for name in ('provider','embedding','download','index_job','network')}
    probes['network'].original_connect=socket.socket.connect
    monkeypatch.setattr('app.services.visual_search.provider.DashScopeProvider.__init__',probes['provider'])
    monkeypatch.setattr('app.services.visual_search.provider.DashScopeProvider.embed',probes['embedding'])
    monkeypatch.setattr('app.services.visual_search.production_catalog.download_image',probes['download'])
    monkeypatch.setattr('app.services.visual_search.service.VisualService.rebuild',probes['index_job'])
    monkeypatch.setattr(socket.socket,'connect',probes['network'])
    return options, tmp_path, probes


def planned(fixture):
    options, tmp, probes=fixture
    report=tool.plan(options,tmp/'plan')
    return report,tmp/'plan'/'report.json'


def test_plan_execute_verify_preserves_all_noncode_data_and_ai(fixture):
    options,tmp,probes=fixture
    before={str(p):binding(p) for p in (tmp/'data').rglob('*') if p.is_file()}
    context=catalog_context(Path(options['catalog_root']))
    report,path=planned(fixture)
    assert before=={str(p):binding(p) for p in (tmp/'data').rglob('*') if p.is_file()}
    assert [r['product_id'] for r in report['mapping']]==[4,18,29,90]
    assert [r['new_code'] for r in report['mapping']]==['250001','250002','250003','250004']
    assert report['warnings']['empty_or_abnormal_code_ids']==[29,90]
    assert report['warnings']['target_code_overlap_ids']==[4,18]
    assert report['ai']['missing_from_mirror']==[29]
    result=tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'execute')
    assert result['state']=='committed'
    assert tool.verify(path)['state']=='verified'
    with closing(connect(options['database'])) as db:
        assert db.execute('SELECT carton_count FROM product_packagings WHERE product_id=90').fetchone()[0]==0
        assert db.execute('SELECT packing_qty FROM product_packagings WHERE product_id=29').fetchone()[0] is None
        assert db.execute('SELECT original_product_code FROM products WHERE id=4').fetchone()[0]=='原-4'
        assert db.execute('SELECT count(*) FROM inventory_movements').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM product_import_batches').fetchone()[0]==1
    assert catalog_context(Path(options['catalog_root']))!=context
    assert catalog_context(Path(options['catalog_root']))['catalog_fingerprint']==context['catalog_fingerprint']
    assert not list(Path(options['catalog_root']).glob('*manifest*'))
    assert report['scope']=='offline-test'
    for probe in probes.values():
        assert probe.call_count==0
    for key,value in before.items():
        if Path(key).name not in {'inventory.db','catalog.sqlite'}:
            assert binding(key)==value
    with pytest.raises(ValueError, match='已有一次性换号'):
        tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'repeat')


@pytest.mark.parametrize('change',['mapping','digest','database','backup','mirror','identity','enabled','wal'])
def test_drift_or_bad_mapping_fails_closed(fixture, change):
    options,tmp,_=fixture
    report,path=planned(fixture)
    if change=='mapping':
        report['mapping'][0]['new_code']='250002'
        report['plan_sha256']=digest({k:v for k,v in report.items() if k!='plan_sha256'})
        path.write_text(json.dumps(report),encoding='utf8')
    elif change=='digest':
        report['plan_sha256']='0'*64
    elif change=='database':
        with sqlite3.connect(options['database']) as db:
            db.execute("UPDATE products SET remark='drift' WHERE id=4")
    elif change=='backup':
        Path(options['backup_database']).write_bytes(b'corrupted')
    elif change=='mirror':
        with sqlite3.connect(Path(options['catalog_root'])/'catalog.sqlite') as db:
            db.execute("DELETE FROM mirror_products WHERE id=4")
    elif change=='identity':
        (Path(options['catalog_root'])/'source.json').write_text('{}')
    elif change=='enabled':
        marker=Path(options['visual_root'])/'runtime'/'ai-search.enabled'
        marker.parent.mkdir();marker.write_text('enabled\n')
    elif change=='wal':
        Path(options['database']+'-wal').write_bytes(b'unknown')
    before=Path(options['database']).read_bytes()
    with pytest.raises((ValueError,sqlite3.Error)):
        tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'rejected')
    assert Path(options['database']).read_bytes()==before


@pytest.mark.parametrize('failure',['business','catalog','invariants'])
def test_transaction_rolls_back_both_databases_and_registry(fixture,monkeypatch,failure):
    options,tmp,_=fixture
    report,path=planned(fixture)
    before=[Path(options['database']).read_bytes(),(Path(options['catalog_root'])/'catalog.sqlite').read_bytes()]
    original=numbering_catalog.apply
    def fail(db,prepared,approval):
        if failure!='business':
            original(db,prepared,approval)
        if failure=='invariants':
            db.execute("UPDATE products SET remark='bad-trigger' WHERE id=4")
        else:
            raise RuntimeError('injected interruption')
    monkeypatch.setattr(numbering_catalog,'apply',fail)
    with pytest.raises((ValueError,RuntimeError)):
        tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'failed')
    assert [Path(options['database']).read_bytes(),(Path(options['catalog_root'])/'catalog.sqlite').read_bytes()]==before
    with closing(connect(options['database'])) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name=?",(tool.REGISTRY,)).fetchall()
    monkeypatch.setattr(numbering_catalog,'apply',original)
    assert tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'retry')['state']=='committed'


def test_report_failure_after_commit_never_reexecutes(fixture,monkeypatch):
    _,tmp,_=fixture
    report,path=planned(fixture)
    original=Path.open
    def fail(p,*args,**kwargs):
        if p.name=='result.json':
            raise OSError('injected disk error')
        return original(p,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail)
    assert tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'commit')['state']=='committed_report_failed'
    assert tool.verify(path)['state']=='verified'
    with pytest.raises(ValueError,match='已有一次性换号'):
        tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'again')


def test_confirmation_and_independent_backup_required(fixture):
    options,tmp,_=fixture
    report,path=planned(fixture)
    with pytest.raises(ValueError,match='显式确认'):
        tool.execute(path,report['plan_sha256'],output=tmp/'notconfirmed')
    with pytest.raises(ValueError,match='完整计划摘要'):
        tool.execute(path,'0'*64,writers_stopped=True,output=tmp/'wrongconfirm')
    options['backup_database']=options['database']
    with pytest.raises(ValueError,match='独立'):
        tool.plan(options,tmp/'samefile')


def test_hardlink_database_rejected(fixture):
    options,tmp,_=fixture
    link=tmp/'link.db'
    os.link(options['database'],link)
    with pytest.raises(ValueError,match='硬链接'):
        tool.plan(options,tmp/'hardlink-plan')


def test_catalog_version_feedback_old_session_rejected(fixture):
    from app.services.visual_search.sources import ProductionSource
    from app.services.visual_search.service import VisualService
    from app.services.visual_search.images import VisualError
    options,tmp,probes=fixture
    root=Path(options['visual_root'])
    source=ProductionSource(Path(options['catalog_root']))
    config=VisualConfig(source_identity=options['namespace'])
    provider=MockProvider()
    service=VisualService(root/'production-indices'/options['namespace'],tmp/'unused',config,provider,
                          source=source,call_lock_root=root)
    with service.store.connection() as db:
        db.execute('INSERT INTO visual_queries VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('old','owner',config.index_key,config.model,config.version,'old.jpg','hash',None,
                    json.dumps({'ids':[4],'catalog':source.context()}),0,'{}','before'))
    report,path=planned(fixture)
    tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'cutover')
    with pytest.raises(VisualError,match='商品目录已更新'):
        service.feedback(None,'owner','old',4,False)
    with service.store.connection() as db:
        assert db.execute('SELECT count(*) FROM visual_feedback').fetchone()[0]==0
    assert provider.calls==0


def test_cli_plan_execute_verify_in_separate_processes(fixture):
    options,tmp,_=fixture
    script=Path(__file__).resolve().parents[1]/'scripts'/'numbering_v2.py'
    def run(*args):
        result=subprocess.run([sys.executable,'-B',str(script),*map(str,args)],
                              capture_output=True,text=True,encoding='utf8',timeout=30)
        assert result.returncode==0, result.stderr
        return json.loads(result.stdout)
    arguments=[]
    for key,value in options.items():
        arguments += ['--'+key.replace('_','-')] + ([] if value is True else [value])
    plan=run('plan',*arguments,'--output',tmp/'cli-plan')
    assert plan['products']==4
    result=run('execute','--plan',tmp/'cli-plan'/'report.json',
               '--confirm-sha256',plan['plan_sha256'],'--confirm-writers-stopped','--output',tmp/'cli-execute')
    assert result['state']=='committed'
    assert run('verify','--plan',tmp/'cli-plan'/'report.json')['products']==4


def test_process_exit_before_commit_retains_recoverable_originals(fixture):
    options,tmp,_=fixture
    report,path=planned(fixture)
    code='''
import os, sys
from app.services import numbering_v2 as tool, numbering_catalog as catalog
original=catalog.apply
def interrupt(*args):
    original(*args)
    os._exit(27)
catalog.apply=interrupt
tool.execute(sys.argv[1],sys.argv[2],writers_stopped=True,output=sys.argv[3])
'''
    child=subprocess.run([sys.executable,'-B','-c',code,str(path),report['plan_sha256'],str(tmp/'crash')],timeout=30)
    assert child.returncode==27
    # Real SQLite hot journals are deliberately not removed by the tool.
    with pytest.raises(ValueError,match='数据库存在日志'):
        tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'unsafe-retry')
    # Let SQLite recover any genuine hot journal, without unlinking anything.
    # A pre-flush/cold journal may remain; the tool must still refuse it.
    for target in (Path(options['database']),Path(options['catalog_root'])/'catalog.sqlite'):
        with closing(sqlite3.connect(target)) as db:
            assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
            assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert Path(options['database']).read_bytes()==Path(options['backup_database']).read_bytes()
    assert (Path(options['catalog_root'])/'catalog.sqlite').read_bytes()==Path(options['backup_catalog']).read_bytes()
    targets=(Path(options['database']),Path(options['catalog_root'])/'catalog.sqlite')
    if any(Path(str(p)+'-journal').exists() for p in targets):
        with pytest.raises(ValueError,match='数据库存在日志'):
            tool.plan(options,tmp/'after-recovery-plan')
    else:
        renewed=tool.plan(options,tmp/'after-recovery-plan')
        assert tool.execute(tmp/'after-recovery-plan'/'report.json',renewed['plan_sha256'],
                            writers_stopped=True,output=tmp/'after-recovery-execute')['state']=='committed'


@pytest.mark.parametrize('kind',['directory_symlink','wal_mode','wrong_revision','unknown_mirror','missing_ai_backup'])
def test_unsafe_paths_modes_and_identities_rejected(fixture,kind):
    options,tmp,_=fixture
    if kind=='directory_symlink':
        link=tmp/'linked-data'
        try:
            link.symlink_to(Path(options['database']).parent,target_is_directory=True)
        except OSError:
            if os.name=='nt':
                # Junctions exercise the same Windows reparse boundary without
                # requiring Developer Mode symlink privileges.
                import _winapi
                _winapi.CreateJunction(str(Path(options['database']).parent),str(link))
            else:
                raise
        options['database']=str(link/'inventory.db')
    elif kind=='wal_mode':
        with sqlite3.connect(options['database']) as db:
            db.execute('PRAGMA journal_mode=WAL')
    elif kind=='wrong_revision':
        with sqlite3.connect(options['database']) as db:
            db.execute("UPDATE alembic_version SET version_num='c8d9e0f1a2b3'")
        shutil.copy2(options['database'],options['backup_database'])
    elif kind=='unknown_mirror':
        with sqlite3.connect(Path(options['catalog_root'])/'catalog.sqlite') as db:
            row=json.loads(db.execute('SELECT document FROM mirror_products LIMIT 1').fetchone()[0])
            row['id']=999; row['product_key']=SOURCE_ID+':999'
            db.execute('INSERT INTO mirror_products VALUES(?,?,?)',(SOURCE_ID,999,json.dumps(row)))
        shutil.copy2(Path(options['catalog_root'])/'catalog.sqlite',options['backup_catalog'])
    else:
        options['backup_catalog']=str(tmp/'not-provided.sqlite')
    before=Path(options['backup_database']).read_bytes()
    with pytest.raises((ValueError,sqlite3.Error)):
        tool.plan(options,tmp/'rejected-plan')
    assert Path(options['backup_database']).read_bytes()==before


def test_mapping_is_persistent_and_immutable(fixture):
    options,tmp,_=fixture
    report,path=planned(fixture)
    tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'execute')
    with closing(connect(options['database'],write=True)) as db:
        assert json.loads(db.execute(f'SELECT document FROM {tool.REGISTRY}').fetchone()[0])==report
        for sql in [f'UPDATE {tool.REGISTRY} SET document=\'{{}}\'', f'DELETE FROM {tool.REGISTRY}',
                    f'INSERT OR REPLACE INTO {tool.REGISTRY} VALUES(1,\'{{}}\')']:
            with pytest.raises(sqlite3.IntegrityError,match='immutable'):
                db.execute(sql)
            db.rollback()
    assert tool.verify(path)['state']=='verified'


def test_inverse_restore_preserves_all_data_and_uses_a_new_catalog_revision(fixture):
    options,tmp,probes=fixture
    report,path=planned(fixture)
    tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'execute')
    version=catalog_context(Path(options['catalog_root']))
    result=tool.restore(path,report['plan_sha256'],writers_stopped=True,output=tmp/'restore')
    assert result['state']=='restored'
    assert tool.verify(path)['state']=='restored_verified'
    restored=catalog_context(Path(options['catalog_root']))
    assert restored['catalog_fingerprint']==version['catalog_fingerprint']
    assert restored['catalog_revision']!=version['catalog_revision']
    with closing(connect(options['database'])) as db:
        assert [list(r) for r in db.execute('SELECT id,product_code FROM products ORDER BY id')]==[[r['product_id'],r['old_code']] for r in report['mapping']]
    with pytest.raises(ValueError,match='已恢复'):
        tool.restore(path,report['plan_sha256'],writers_stopped=True,output=tmp/'again')
    with pytest.raises(ValueError,match='已有一次性'):
        tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'again-execute')
    assert all(p.call_count==0 for p in probes.values())


@pytest.mark.parametrize('mutation',[
    "UPDATE products SET remark='new business note' WHERE id=4",
    "UPDATE products SET original_product_code='new supplier code' WHERE id=4",
    "UPDATE products SET updated_at='2026-10-29' WHERE id=4",
    "UPDATE product_packagings SET carton_count=carton_count+1 WHERE product_id=4",
    "UPDATE inventory_movements SET remark='new movement note'",
    "INSERT INTO product_code_months VALUES('202610',2)",
    "INSERT INTO inventory_movements(product_id,warehouse_id,movement_type,quantity,before_carton_count,after_carton_count,created_at) VALUES(4,1,'IN',1,9,10,'2026-10-29')",
    "UPDATE products SET warehouse_id=2 WHERE id=4",
    "UPDATE product_packagings SET carton_count=CASE WHEN product_id=4 THEN carton_count+1 WHEN product_id=18 THEN carton_count-2 ELSE carton_count END",
])
def test_restore_rejects_every_kind_of_post_cutover_business_write(fixture,mutation):
    options,tmp,_=fixture
    report,path=planned(fixture)
    tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'execute')
    with closing(sqlite3.connect(options['database'])) as db,db:
        db.execute(mutation)
    targets=[Path(options['database']),Path(options['catalog_root'])/'catalog.sqlite']
    before=[p.read_bytes() for p in targets]
    with pytest.raises(ValueError):
        tool.restore(path,report['plan_sha256'],writers_stopped=True,output=tmp/'rejected')
    assert [p.read_bytes() for p in targets]==before


@pytest.mark.parametrize('stage',['temporary_codes','business_updated','mirror_updated','before_commit'])
def test_each_transaction_stage_rolls_back_both_databases(fixture,stage):
    options,tmp,_=fixture
    report,path=planned(fixture)
    targets=[Path(options['database']),Path(options['catalog_root'])/'catalog.sqlite']
    before=[p.read_bytes() for p in targets]
    def fail(current):
        if stage==current: raise RuntimeError(stage)
    with pytest.raises(RuntimeError,match=stage):
        tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'failed',_fault=fail)
    assert [p.read_bytes() for p in targets]==before


def test_restore_failure_rolls_back_both_databases(fixture):
    options,tmp,_=fixture
    report,path=planned(fixture)
    tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'execute')
    targets=[Path(options['database']),Path(options['catalog_root'])/'catalog.sqlite']
    before=[p.read_bytes() for p in targets]
    def fail(stage): raise RuntimeError(stage)
    with pytest.raises(RuntimeError,match='restore_updated'):
        tool.restore(path,report['plan_sha256'],writers_stopped=True,output=tmp/'failed',_fault=fail)
    assert [p.read_bytes() for p in targets]==before
    assert tool.verify(path)['state']=='verified'


def test_pending_consistency_needs_explicit_test_scope(fixture):
    options,tmp,_=fixture
    options.pop('offline_test')
    with pytest.raises(ValueError,match='跨库一致性'):
        tool.plan(options,tmp/'rejected')


def test_partial_or_wrong_consistency_evidence_is_blocked(fixture):
    options,tmp,_=fixture
    options.pop('offline_test')
    evidence=tmp/'evidence.json'
    evidence.write_text(json.dumps({'format':'numbering-v2-consistency-v1','reviewed':True,
                                   'backup_set':'synthetic only','method':'synthetic fixture','sha256':{}}))
    options['consistency_evidence']=str(evidence)
    with pytest.raises(ValueError,match='一致备份证据'):
        tool.plan(options,tmp/'rejected')


@pytest.mark.parametrize('missing',[False,True])
def test_mirror_old_code_drift_or_missing_fields_are_blockers(fixture,missing):
    options,tmp,_=fixture
    target=Path(options['catalog_root'])/'catalog.sqlite'
    with closing(sqlite3.connect(target)) as db,db:
        row=json.loads(db.execute('SELECT document FROM mirror_products WHERE id=4').fetchone()[0])
        if missing: row.pop('product_code')
        else: row['product_code']='stale'
        db.execute('UPDATE mirror_products SET document=? WHERE id=4',(json.dumps(row),))
    shutil.copy2(target,options['backup_catalog'])
    with pytest.raises(ValueError,match='漂移|缺失字段'):
        tool.plan(options,tmp/'rejected')


def test_zero_manifest_and_normal_future_sync_keeps_revision(fixture):
    from app.services.visual_search.production_catalog import publish
    options,tmp,_=fixture
    report,path=planned(fixture)
    tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'execute')
    root=Path(options['catalog_root'])
    prior=catalog_context(root)
    with closing(connect(root/'catalog.sqlite')) as db:
        metadata={k:json.loads(v) for k,v in db.execute('SELECT key,document FROM mirror_meta')}
        records=[json.loads(r[0]) for r in db.execute('SELECT document FROM mirror_products')]
    snapshot={'products':records,'categories':metadata['categories'],'warehouses':metadata['warehouses']}
    publish(root,metadata['identity'],snapshot,records,{'scope':'full','finished_at':'synthetic-later','catalog_fingerprint':fingerprint(snapshot)})
    current=catalog_context(root)
    assert current['catalog_revision']==prior['catalog_revision']
    assert current['catalog_fingerprint']!=prior['catalog_fingerprint']
    assert not list(root.glob('*manifest*'))


def test_valid_synthetic_consistency_evidence_is_hash_bound(fixture):
    options,tmp,_=fixture
    preliminary,_=planned(fixture)
    options.pop('offline_test')
    evidence=tmp/'synthetic-evidence.json'
    protected=preliminary['ai']['protected']
    evidence.write_text(json.dumps({'format':'numbering-v2-consistency-v1','reviewed':True,
        'backup_set':'SYNTHETIC ONLY','method':'fixture initialization, no writers',
        'sha256':{'business':binding(options['database'])['sha256'],
                  'catalog':binding(Path(options['catalog_root'])/'catalog.sqlite')['sha256'],
                  'index':protected[str(Path('production-indices')/options['namespace']/'index.db')]['sha256'],
                  'budget':protected['daily-budget.sqlite3']['sha256']}}),encoding='utf8')
    options['consistency_evidence']=str(evidence)
    report=tool.plan(options,tmp/'with-evidence')
    assert report['scope']=='reviewed-offline-backup'
    assert tool.execute(tmp/'with-evidence'/'report.json',report['plan_sha256'],writers_stopped=True,output=tmp/'execute')['state']=='committed'


def test_exact_old_internal_code_search_and_supplier_code_are_separate(fixture,monkeypatch):
    from app.main import app
    from app.database import get_db
    from fastapi.testclient import TestClient
    options,tmp,probes=fixture
    report,path=planned(fixture)
    tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'execute')
    engine=create_engine('sqlite:///'+Path(options['database']).as_posix())
    def dependency():
        with Session(engine) as db: yield db
    app.dependency_overrides[get_db]=dependency
    try:
        # Windows' asyncio loop creates a localhost socketpair during startup.
        # Reinstall the external-network tripwire before the in-process request.
        with monkeypatch.context() as startup:
            startup.setattr(socket.socket,'connect',probes['network'].original_connect)
            client=TestClient(app).__enter__()
        try:
            items=client.get('/api/products',params={'search':'250002','status':'all'}).json()['items']
            assert {r['id'] for r in items}=={4,18}
            assert next(r for r in items if r['id']==4)['original_product_code']=='原-4'
        finally:
            client.__exit__(None,None,None)
    finally:
        app.dependency_overrides.clear(); engine.dispose()


def test_removing_immutability_trigger_is_detected(fixture):
    options,tmp,_=fixture
    report,path=planned(fixture)
    tool.execute(path,report['plan_sha256'],writers_stopped=True,output=tmp/'execute')
    with closing(sqlite3.connect(options['database'])) as db:
        db.execute('DROP TRIGGER numbering_mapping_no_update')
    with pytest.raises(ValueError,match='不可变'):
        tool.verify(path)


def test_abnormal_old_code_canonical_mapping_is_exact(fixture):
    options,tmp,_=fixture
    with closing(sqlite3.connect(options['database'])) as db,db:
        db.execute("UPDATE products SET product_code='=2+2' WHERE id=29")
    shutil.copy2(options['database'],options['backup_database'])
    report,path=planned(fixture)
    assert next(r for r in report['mapping'] if r['product_id']==29)['old_code']=='=2+2'
    assert next(r for r in json.loads(path.read_text(encoding='utf8'))['mapping'] if r['product_id']==29)['old_code']=='=2+2'
