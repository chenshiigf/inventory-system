"""Visual index, resumable jobs, call ledger, query references and feedback."""
from alembic import op

revision = "vs01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    statements = [
        """CREATE TABLE visual_embeddings (
            product_id INTEGER NOT NULL, index_key TEXT NOT NULL,
            image_identifier TEXT, image_hash TEXT,
            embedding_model TEXT NOT NULL, embedding_dimension INTEGER NOT NULL,
            embedding_version TEXT NOT NULL, preprocessing_version TEXT NOT NULL,
            embedding_vector BLOB, indexed_at TEXT,
            indexing_status TEXT NOT NULL CHECK(indexing_status IN ('ready','failed','skipped')),
            error TEXT, PRIMARY KEY(product_id, index_key))""",
        """CREATE TABLE visual_jobs (
            id TEXT PRIMARY KEY, index_key TEXT NOT NULL, owner TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('running','completed','failed','interrupted')),
            max_items INTEGER NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
            succeeded INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL, finished_at TEXT, error TEXT)""",
        """CREATE UNIQUE INDEX visual_single_running_job ON visual_jobs(status) WHERE status='running'""",
        """CREATE TABLE visual_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, index_key TEXT NOT NULL,
            purpose TEXT NOT NULL, task_id TEXT NOT NULL, usage TEXT NOT NULL,
            request_id TEXT, elapsed_ms REAL NOT NULL, error TEXT, created_at TEXT NOT NULL)""",
        """CREATE TABLE visual_queries (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, index_key TEXT NOT NULL,
            embedding_model TEXT NOT NULL, embedding_version TEXT NOT NULL,
            image_reference TEXT NOT NULL, source_hash TEXT NOT NULL, crop TEXT,
            candidate_ids TEXT NOT NULL, elapsed_ms REAL NOT NULL,
            usage TEXT NOT NULL, created_at TEXT NOT NULL)""",
        """CREATE TABLE visual_feedback (
            query_id TEXT PRIMARY KEY REFERENCES visual_queries(id) ON DELETE CASCADE,
            selected_product_id INTEGER, no_match INTEGER NOT NULL CHECK(no_match IN (0,1)),
            owner TEXT NOT NULL, created_at TEXT NOT NULL,
            CHECK((no_match=1 AND selected_product_id IS NULL) OR
                  (no_match=0 AND selected_product_id IS NOT NULL)))""",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade():
    for name in ["visual_feedback", "visual_queries", "visual_calls", "visual_jobs", "visual_embeddings"]:
        op.execute(f"DROP TABLE {name}")
