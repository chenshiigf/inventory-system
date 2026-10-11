"""Source context and durable image-content cache; existing vs01 remains readable."""
from alembic import op

revision = "vs02"
down_revision = "vs01"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE visual_image_vectors (
        index_key TEXT NOT NULL, image_hash TEXT NOT NULL, embedding_vector BLOB NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY(index_key,image_hash))""")
    op.execute("""CREATE TABLE visual_source_context (
        index_key TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_identity TEXT NOT NULL,
        catalog_fingerprint TEXT, model TEXT NOT NULL, dimension INTEGER NOT NULL,
        version TEXT NOT NULL, preprocessing_version TEXT NOT NULL, updated_at TEXT NOT NULL)""")


def downgrade():
    op.execute("DROP TABLE visual_source_context")
    op.execute("DROP TABLE visual_image_vectors")
