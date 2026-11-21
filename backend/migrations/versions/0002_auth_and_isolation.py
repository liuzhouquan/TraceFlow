"""0002 auth and isolation schema

Revision ID: 0002_auth_and_isolation
Revises: 0001_initial
Create Date: 2026-11-21 10:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_auth_and_isolation"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users 表增加认证安全字段
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("email", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("hashed_password", sa.String(255), nullable=True))
        batch_op.add_column(
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=True,
            )
        )
        batch_op.create_index("ix_users_email", ["email"], unique=True)

    # 2. repositories 表增加创建者与数据隔离字段
    with op.batch_alter_table("repositories") as batch_op:
        batch_op.add_column(
            sa.Column(
                "created_by",
                sa.Uuid(),
                sa.ForeignKey("users.id", name="fk_repositories_created_by_users"),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column("is_public", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.create_index("ix_repositories_created_by", ["created_by"])
        batch_op.create_index("ix_repositories_is_public", ["is_public"])

    # 3. 创建 repository_members 关联表
    op.create_table(
        "repository_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "repo_id",
            sa.Uuid(),
            sa.ForeignKey("repositories.id", name="fk_repository_members_repo_id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", name="fk_repository_members_user_id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(80), server_default="developer", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_repository_members_repo_id", "repository_members", ["repo_id"])
    op.create_index("ix_repository_members_user_id", "repository_members", ["user_id"])
    op.create_index("ix_repository_members_role", "repository_members", ["role"])


def downgrade() -> None:
    # 1. 删除 repository_members 表
    op.drop_table("repository_members")

    # 2. 回退 repositories 表扩展字段
    with op.batch_alter_table("repositories") as batch_op:
        batch_op.drop_index("ix_repositories_is_public")
        batch_op.drop_column("is_public")
        batch_op.drop_index("ix_repositories_created_by")
        batch_op.drop_column("created_by")

    # 3. 回退 users 表扩展字段
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_email")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("is_active")
        batch_op.drop_column("hashed_password")
        batch_op.drop_column("email")
