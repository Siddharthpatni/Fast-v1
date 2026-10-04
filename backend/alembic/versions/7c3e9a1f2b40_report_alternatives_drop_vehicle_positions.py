"""report alternatives; drop unused vehicle_positions

Revision ID: 7c3e9a1f2b40
Revises: 1b1d342e49ca
Create Date: 2026-10-04 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geometry

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c3e9a1f2b40"
down_revision: str | None = "1b1d342e49ca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # What the report recommends instead, as read by the language model
    op.add_column("disruptions", sa.Column("alternatives", sa.Text(), nullable=True))
    op.add_column("disruptions", sa.Column("alternative_lines", sa.Text(), nullable=True))
    # Never written or read: live vehicles come from GBFS and GTFS-RT, held in memory
    op.drop_index(op.f("ix_vehicle_positions_vehicle_id"), table_name="vehicle_positions")
    op.drop_geospatial_index(
        "idx_vehicle_positions_location",
        table_name="vehicle_positions",
        postgresql_using="gist",
        column_name="location",
    )
    op.drop_geospatial_table("vehicle_positions")


def downgrade() -> None:
    op.create_geospatial_table(
        "vehicle_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.String(length=100), nullable=False),
        sa.Column("mode", sa.String(length=50), nullable=False),
        sa.Column(
            "location",
            Geometry(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeomFromEWKT",
                name="geometry",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("heading", sa.Float(), nullable=True),
        sa.Column("speed", sa.Float(), nullable=True),
        sa.Column("occupancy", sa.Float(), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_geospatial_index(
        "idx_vehicle_positions_location",
        "vehicle_positions",
        ["location"],
        unique=False,
        postgresql_using="gist",
        postgresql_ops={},
    )
    op.create_index(
        op.f("ix_vehicle_positions_vehicle_id"), "vehicle_positions", ["vehicle_id"], unique=False
    )
    op.drop_column("disruptions", "alternative_lines")
    op.drop_column("disruptions", "alternatives")
