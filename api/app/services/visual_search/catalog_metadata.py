"""Catalog-only reads: no service, provider, vector store or budget imports."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from app.services.numbering_io import checked_path

SOURCE_ID = 'aliyun-inventory-production'
MAGIC = 'inventory-production-catalog-v1'


def fingerprint(value):
    # The existing sync snapshot serialization is part of the catalog contract.
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def catalog_context(root):
    root = checked_path(root)
    identity = json.loads(checked_path(root / 'source.json', existing=True).read_text(encoding='utf8'))
    if not isinstance(identity, dict) or identity.get('format') != MAGIC or identity.get('source_id') != SOURCE_ID:
        raise ValueError('生产镜像身份不符')
    path = checked_path(root / 'catalog.sqlite', existing=True)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        meta = {k: json.loads(v) for k, v in db.execute('SELECT key,document FROM mirror_meta')}
    if meta.get('identity') != identity:
        raise ValueError('镜像文件与目录身份不一致')
    version = meta['last_report']['catalog_fingerprint']
    if not isinstance(version, str) or not re.fullmatch(r'[a-f0-9]{64}', version):
        raise ValueError('目录版本无效')
    context = {'source': 'production-catalog', 'source_id': SOURCE_ID,
               'index_namespace': 'production-' + fingerprint(identity), 'catalog_fingerprint': version}
    cutover = meta.get('numbering_cutover')
    if cutover is not None:
        if not isinstance(cutover, dict):
            raise ValueError('目录换号版本登记异常')
        revision = cutover.get('commit_sha256')
        if (cutover.get('state') != 'committed' or not isinstance(revision, str) or
                not re.fullmatch(r'[a-f0-9]{64}', revision) or
                fingerprint({k: v for k, v in cutover.items() if k != 'commit_sha256'}) != revision):
            raise ValueError('目录换号版本登记异常')
        context['catalog_revision'] = revision
    return context
