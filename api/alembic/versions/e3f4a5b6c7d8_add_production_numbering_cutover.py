"""Record one production numbering cutover in the same transaction as codes."""
from alembic import op

revision = 'e3f4a5b6c7d8'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE production_numbering_cutovers (
        id INTEGER NOT NULL PRIMARY KEY CHECK(id=1), operation_id TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK(state='committed'), document TEXT NOT NULL)""")


def downgrade():
    if op.get_bind().exec_driver_sql('SELECT count(*) FROM production_numbering_cutovers').scalar():
        raise RuntimeError('正式换号已经提交，禁止删除登记或回退为旧编号生成代码')
    op.drop_table('production_numbering_cutovers')
