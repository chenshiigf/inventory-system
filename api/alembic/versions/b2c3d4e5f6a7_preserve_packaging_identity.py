"""Prevent reuse of deleted packaging identities.

Revision ID: b2c3d4e5f6a7
Revises: e1f2a3b4c5d6
"""

from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # SQLite's driver does not start a transaction for DDL by default. Keep
    # the rebuild and link restoration atomic, also with foreign keys enabled.
    if connection.dialect.name == "sqlite":
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")

    links = connection.execute(
        sa.text(
            "SELECT id, product_packaging_id FROM inventory_movements "
            "WHERE product_packaging_id IS NOT NULL"
        )
    ).mappings().all()

    # Batch recreation copies packaging IDs, stock, fields, constraints and
    # indexes. Dropping the old table SET NULLs movement links when FK checks
    # are enabled, so restore only those links inside the same transaction.
    with op.batch_alter_table(
        "product_packagings",
        recreate="always",
        table_kwargs={"sqlite_autoincrement": True},
    ):
        pass

    if links:
        connection.execute(
            sa.text(
                "UPDATE inventory_movements "
                "SET product_packaging_id = :product_packaging_id WHERE id = :id"
            ),
            [dict(link) for link in links],
        )


def downgrade() -> None:
    # Removing this guarantee could make an old stock request target a new
    # packaging. Do not silently weaken persisted identity protection.
    raise RuntimeError("Cannot downgrade packaging identity protection.")
