"""0001 initial baseline schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_type", sa.String(length=100), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_analysis_results_analysis_type"),
        "analysis_results",
        ["analysis_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_analysis_results_target_id"), "analysis_results", ["target_id"], unique=False
    )
    op.create_index(
        op.f("ix_analysis_results_target_type"), "analysis_results", ["target_type"], unique=False
    )
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("github_token_encrypted", sa.String(length=None), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("full_name", sa.String(length=400), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("api_base_url", sa.String(length=None), nullable=True),
        sa.Column("clone_url", sa.String(length=None), nullable=True),
        sa.Column("local_path", sa.String(length=None), nullable=True),
        sa.Column("checkout_mode", sa.String(length=80), nullable=False),
        sa.Column("github_token_encrypted", sa.String(length=None), nullable=True),
        sa.Column("description", sa.String(length=None), nullable=True),
        sa.Column("default_branch", sa.String(length=200), nullable=True),
        sa.Column("github_id", sa.Integer(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_repositories_name"), "repositories", ["name"], unique=False)
    op.create_index(op.f("ix_repositories_full_name"), "repositories", ["full_name"], unique=True)
    op.create_index(op.f("ix_repositories_owner"), "repositories", ["owner"], unique=False)
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=None), nullable=True),
        sa.Column("repo_ids", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index(op.f("ix_workspaces_name"), "workspaces", ["name"], unique=False)
    op.create_index(op.f("ix_workspaces_created_by"), "workspaces", ["created_by"], unique=False)
    op.create_index(op.f("ix_workspaces_created_at"), "workspaces", ["created_at"], unique=False)
    op.create_table(
        "action_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("draft_type", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.String(length=None), nullable=False),
        sa.Column("risk_level", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.String(length=None), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"]),
    )
    op.create_index(
        op.f("ix_action_drafts_target_type"), "action_drafts", ["target_type"], unique=False
    )
    op.create_index(op.f("ix_action_drafts_status"), "action_drafts", ["status"], unique=False)
    op.create_index(
        op.f("ix_action_drafts_confirmed_by"), "action_drafts", ["confirmed_by"], unique=False
    )
    op.create_index(
        op.f("ix_action_drafts_created_at"), "action_drafts", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_action_drafts_created_by"), "action_drafts", ["created_by"], unique=False
    )
    op.create_index(
        op.f("ix_action_drafts_target_id"), "action_drafts", ["target_id"], unique=False
    )
    op.create_index(
        op.f("ix_action_drafts_draft_type"), "action_drafts", ["draft_type"], unique=False
    )
    op.create_index(
        op.f("ix_action_drafts_risk_level"), "action_drafts", ["risk_level"], unique=False
    )
    op.create_index(op.f("ix_action_drafts_repo_id"), "action_drafts", ["repo_id"], unique=False)
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("repo_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=160), nullable=False),
        sa.Column("target_type", sa.String(length=100), nullable=True),
        sa.Column("target_id", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_target_type"), "audit_logs", ["target_type"], unique=False)
    op.create_index(op.f("ix_audit_logs_user_id"), "audit_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_repo_id"), "audit_logs", ["repo_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_target_id"), "audit_logs", ["target_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_audit_logs_status"), "audit_logs", ["status"], unique=False)
    op.create_table(
        "code_symbols",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("language", sa.String(length=80), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("commit_sha", sa.String(length=120), nullable=True),
        sa.Column("pr_id", sa.Uuid(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(
        op.f("ix_code_symbols_commit_sha"), "code_symbols", ["commit_sha"], unique=False
    )
    op.create_index(
        op.f("ix_code_symbols_created_at"), "code_symbols", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_code_symbols_kind"), "code_symbols", ["kind"], unique=False)
    op.create_index(op.f("ix_code_symbols_path"), "code_symbols", ["path"], unique=False)
    op.create_index(op.f("ix_code_symbols_pr_id"), "code_symbols", ["pr_id"], unique=False)
    op.create_index(op.f("ix_code_symbols_language"), "code_symbols", ["language"], unique=False)
    op.create_index(op.f("ix_code_symbols_repo_id"), "code_symbols", ["repo_id"], unique=False)
    op.create_index(op.f("ix_code_symbols_pr_number"), "code_symbols", ["pr_number"], unique=False)
    op.create_index(op.f("ix_code_symbols_name"), "code_symbols", ["name"], unique=False)
    op.create_index(op.f("ix_code_symbols_branch"), "code_symbols", ["branch"], unique=False)
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(op.f("ix_conversations_status"), "conversations", ["status"], unique=False)
    op.create_index(
        op.f("ix_conversations_updated_at"), "conversations", ["updated_at"], unique=False
    )
    op.create_index(op.f("ix_conversations_repo_id"), "conversations", ["repo_id"], unique=False)
    op.create_index(
        op.f("ix_conversations_created_at"), "conversations", ["created_at"], unique=False
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.String(length=None), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(op.f("ix_documents_source_type"), "documents", ["source_type"], unique=False)
    op.create_index(op.f("ix_documents_source_id"), "documents", ["source_id"], unique=False)
    op.create_index(op.f("ix_documents_repo_id"), "documents", ["repo_id"], unique=False)
    op.create_table(
        "issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("github_issue_id", sa.Integer(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.String(length=None), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("assignees", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(op.f("ix_issues_number"), "issues", ["number"], unique=False)
    op.create_index(op.f("ix_issues_repo_id"), "issues", ["repo_id"], unique=False)
    op.create_table(
        "knowledge_base_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_provider", sa.String(length=80), nullable=False),
        sa.Column("embedding_model", sa.String(length=240), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("retrieval_method", sa.String(length=40), nullable=False),
        sa.Column("rerank_enabled", sa.Boolean(), nullable=False),
        sa.Column("rerank_provider", sa.String(length=80), nullable=False),
        sa.Column("rerank_model", sa.String(length=240), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("score_threshold_enabled", sa.Boolean(), nullable=False),
        sa.Column("score_threshold", sa.Float(), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(
        op.f("ix_knowledge_base_configs_repo_id"),
        "knowledge_base_configs",
        ["repo_id"],
        unique=True,
    )
    op.create_table(
        "knowledge_graph_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("from_type", sa.String(length=80), nullable=False),
        sa.Column("from_id", sa.String(length=180), nullable=False),
        sa.Column("to_type", sa.String(length=80), nullable=False),
        sa.Column("to_id", sa.String(length=180), nullable=False),
        sa.Column("relation", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(
        op.f("ix_knowledge_graph_edges_repo_id"), "knowledge_graph_edges", ["repo_id"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_graph_edges_created_at"),
        "knowledge_graph_edges",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_graph_edges_to_id"), "knowledge_graph_edges", ["to_id"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_graph_edges_relation"),
        "knowledge_graph_edges",
        ["relation"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_graph_edges_from_id"), "knowledge_graph_edges", ["from_id"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_graph_edges_from_type"),
        "knowledge_graph_edges",
        ["from_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_graph_edges_to_type"), "knowledge_graph_edges", ["to_type"], unique=False
    )
    op.create_table(
        "knowledge_source_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("stored_path", sa.String(length=None), nullable=False),
        sa.Column("content_type", sa.String(length=200), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.String(length=None), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(
        op.f("ix_knowledge_source_documents_status"),
        "knowledge_source_documents",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_source_documents_created_at"),
        "knowledge_source_documents",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_source_documents_checksum"),
        "knowledge_source_documents",
        ["checksum"],
        unique=False,
    )
    op.create_index(
        op.f("ix_knowledge_source_documents_repo_id"),
        "knowledge_source_documents",
        ["repo_id"],
        unique=False,
    )
    op.create_table(
        "project_index_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("fingerprint", sa.String(length=200), nullable=False),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("docs_indexed", sa.Integer(), nullable=False),
        sa.Column("docs_total", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.String(length=None), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(
        op.f("ix_project_index_states_repo_id"), "project_index_states", ["repo_id"], unique=True
    )
    op.create_index(
        op.f("ix_project_index_states_status"), "project_index_states", ["status"], unique=False
    )
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("github_pr_id", sa.Integer(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("body", sa.String(length=None), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("base_branch", sa.String(length=200), nullable=True),
        sa.Column("head_branch", sa.String(length=200), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(op.f("ix_pull_requests_repo_id"), "pull_requests", ["repo_id"], unique=False)
    op.create_index(op.f("ix_pull_requests_number"), "pull_requests", ["number"], unique=False)
    op.create_table(
        "retrieval_test_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.String(length=None), nullable=False),
        sa.Column("retrieval_config", sa.JSON(), nullable=False),
        sa.Column("results_json", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(
        op.f("ix_retrieval_test_runs_repo_id"), "retrieval_test_runs", ["repo_id"], unique=False
    )
    op.create_index(
        op.f("ix_retrieval_test_runs_created_at"),
        "retrieval_test_runs",
        ["created_at"],
        unique=False,
    )
    op.create_table(
        "thread_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=True),
        sa.Column("summary", sa.String(length=None), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("open_questions", sa.JSON(), nullable=False),
        sa.Column("tasks", sa.JSON(), nullable=False),
        sa.Column("user_preferences", sa.JSON(), nullable=False),
        sa.Column("repo_context", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("sessions_incorporated", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(op.f("ix_thread_memories_repo_id"), "thread_memories", ["repo_id"], unique=True)
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("github_run_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("conclusion", sa.String(length=80), nullable=True),
        sa.Column("html_url", sa.String(length=None), nullable=True),
        sa.Column("jobs", sa.JSON(), nullable=False),
        sa.Column("logs_text", sa.String(length=None), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(op.f("ix_workflow_runs_repo_id"), "workflow_runs", ["repo_id"], unique=False)
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("user_message", sa.String(length=None), nullable=False),
        sa.Column("route", sa.String(length=100), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("final_answer", sa.String(length=None), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(
        op.f("ix_agent_runs_conversation_id"), "agent_runs", ["conversation_id"], unique=False
    )
    op.create_table(
        "agent_workflow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("goal", sa.String(length=None), nullable=False),
        sa.Column("spec_json", sa.JSON(), nullable=False),
        sa.Column("observation_json", sa.JSON(), nullable=False),
        sa.Column("final_answer", sa.String(length=None), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index(
        op.f("ix_agent_workflow_runs_conversation_id"),
        "agent_workflow_runs",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_workflow_runs_created_at"),
        "agent_workflow_runs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_workflow_runs_repo_id"), "agent_workflow_runs", ["repo_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_workflow_runs_status"), "agent_workflow_runs", ["status"], unique=False
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("content", sa.String(length=None), nullable=False),
        sa.Column("route", sa.String(length=100), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index(
        op.f("ix_chat_messages_created_at"), "chat_messages", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_chat_messages_repo_id"), "chat_messages", ["repo_id"], unique=False)
    op.create_index(op.f("ix_chat_messages_role"), "chat_messages", ["role"], unique=False)
    op.create_index(
        op.f("ix_chat_messages_conversation_id"), "chat_messages", ["conversation_id"], unique=False
    )
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("user_message_id", sa.Uuid(), nullable=True),
        sa.Column("assistant_message_id", sa.Uuid(), nullable=True),
        sa.Column("route", sa.String(length=100), nullable=False),
        sa.Column("transcript_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=None), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index(
        op.f("ix_chat_sessions_user_message_id"), "chat_sessions", ["user_message_id"], unique=False
    )
    op.create_index(
        op.f("ix_chat_sessions_created_at"), "chat_sessions", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_chat_sessions_status"), "chat_sessions", ["status"], unique=False)
    op.create_index(
        op.f("ix_chat_sessions_conversation_id"), "chat_sessions", ["conversation_id"], unique=False
    )
    op.create_index(
        op.f("ix_chat_sessions_assistant_message_id"),
        "chat_sessions",
        ["assistant_message_id"],
        unique=False,
    )
    op.create_index(op.f("ix_chat_sessions_repo_id"), "chat_sessions", ["repo_id"], unique=False)
    op.create_table(
        "code_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("source_symbol_id", sa.Uuid(), nullable=True),
        sa.Column("target_symbol_id", sa.Uuid(), nullable=True),
        sa.Column("source_name", sa.String(length=300), nullable=True),
        sa.Column("target_name", sa.String(length=300), nullable=False),
        sa.Column("relation_type", sa.String(length=80), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("pr_id", sa.Uuid(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_symbol_id"], ["code_symbols.id"]),
        sa.ForeignKeyConstraint(["target_symbol_id"], ["code_symbols.id"]),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
    )
    op.create_index(
        op.f("ix_code_relations_relation_type"), "code_relations", ["relation_type"], unique=False
    )
    op.create_index(op.f("ix_code_relations_repo_id"), "code_relations", ["repo_id"], unique=False)
    op.create_index(
        op.f("ix_code_relations_pr_number"), "code_relations", ["pr_number"], unique=False
    )
    op.create_index(op.f("ix_code_relations_path"), "code_relations", ["path"], unique=False)
    op.create_index(
        op.f("ix_code_relations_target_symbol_id"),
        "code_relations",
        ["target_symbol_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_code_relations_source_name"), "code_relations", ["source_name"], unique=False
    )
    op.create_index(op.f("ix_code_relations_branch"), "code_relations", ["branch"], unique=False)
    op.create_index(
        op.f("ix_code_relations_created_at"), "code_relations", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_code_relations_target_name"), "code_relations", ["target_name"], unique=False
    )
    op.create_index(op.f("ix_code_relations_pr_id"), "code_relations", ["pr_id"], unique=False)
    op.create_index(
        op.f("ix_code_relations_source_symbol_id"),
        "code_relations",
        ["source_symbol_id"],
        unique=False,
    )
    op.create_table(
        "conversation_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.String(length=None), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("open_questions", sa.JSON(), nullable=False),
        sa.Column("tasks", sa.JSON(), nullable=False),
        sa.Column("user_preferences", sa.JSON(), nullable=False),
        sa.Column("repo_context", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("sessions_incorporated", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index(
        op.f("ix_conversation_memories_repo_id"), "conversation_memories", ["repo_id"], unique=False
    )
    op.create_index(
        op.f("ix_conversation_memories_conversation_id"),
        "conversation_memories",
        ["conversation_id"],
        unique=True,
    )
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.String(length=None), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index(
        op.f("ix_evidence_items_source_type"), "evidence_items", ["source_type"], unique=False
    )
    op.create_index(
        op.f("ix_evidence_items_conversation_id"),
        "evidence_items",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(op.f("ix_evidence_items_repo_id"), "evidence_items", ["repo_id"], unique=False)
    op.create_index(
        op.f("ix_evidence_items_source_id"), "evidence_items", ["source_id"], unique=False
    )
    op.create_index(
        op.f("ix_evidence_items_created_at"), "evidence_items", ["created_at"], unique=False
    )
    op.create_table(
        "pr_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pr_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("additions", sa.Integer(), nullable=False),
        sa.Column("deletions", sa.Integer(), nullable=False),
        sa.Column("patch", sa.String(length=None), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"]),
    )
    op.create_index(op.f("ix_pr_files_pr_id"), "pr_files", ["pr_id"], unique=False)
    op.create_table(
        "pr_review_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pr_id", sa.Uuid(), nullable=False),
        sa.Column("github_comment_id", sa.Integer(), nullable=True),
        sa.Column("body", sa.String(length=None), nullable=True),
        sa.Column("path", sa.String(length=500), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["pr_id"], ["pull_requests.id"]),
    )
    op.create_index(
        op.f("ix_pr_review_comments_pr_id"), "pr_review_comments", ["pr_id"], unique=False
    )
    op.create_table(
        "recall_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("query", sa.String(length=None), nullable=False),
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("results_json", sa.JSON(), nullable=False),
        sa.Column("citations_json", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index(
        op.f("ix_recall_events_created_at"), "recall_events", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_recall_events_repo_id"), "recall_events", ["repo_id"], unique=False)
    op.create_index(op.f("ix_recall_events_status"), "recall_events", ["status"], unique=False)
    op.create_index(
        op.f("ix_recall_events_tool_name"), "recall_events", ["tool_name"], unique=False
    )
    op.create_index(
        op.f("ix_recall_events_conversation_id"), "recall_events", ["conversation_id"], unique=False
    )
    op.create_table(
        "agent_task_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.String(length=120), nullable=False),
        sa.Column("agent_name", sa.String(length=120), nullable=False),
        sa.Column("task_type", sa.String(length=120), nullable=False),
        sa.Column("claim_json", sa.JSON(), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("error", sa.String(length=None), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workflow_id"], ["agent_workflow_runs.id"]),
    )
    op.create_index(
        op.f("ix_agent_task_runs_task_id"), "agent_task_runs", ["task_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_task_runs_agent_name"), "agent_task_runs", ["agent_name"], unique=False
    )
    op.create_index(op.f("ix_agent_task_runs_status"), "agent_task_runs", ["status"], unique=False)
    op.create_index(
        op.f("ix_agent_task_runs_task_type"), "agent_task_runs", ["task_type"], unique=False
    )
    op.create_index(
        op.f("ix_agent_task_runs_workflow_id"), "agent_task_runs", ["workflow_id"], unique=False
    )
    op.create_table(
        "chat_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_message_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("rating", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=80), nullable=True),
        sa.Column("comment", sa.String(length=None), nullable=True),
        sa.Column("review_status", sa.String(length=40), nullable=False),
        sa.Column("review_note", sa.String(length=None), nullable=True),
        sa.Column("notification_status", sa.String(length=40), nullable=False),
        sa.Column("notification_error", sa.String(length=None), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_chat_feedback_user_id"), "chat_feedback", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_chat_feedback_conversation_id"), "chat_feedback", ["conversation_id"], unique=False
    )
    op.create_index(op.f("ix_chat_feedback_repo_id"), "chat_feedback", ["repo_id"], unique=False)
    op.create_index(
        op.f("ix_chat_feedback_review_status"), "chat_feedback", ["review_status"], unique=False
    )
    op.create_index(
        op.f("ix_chat_feedback_created_at"), "chat_feedback", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_chat_feedback_run_id"), "chat_feedback", ["run_id"], unique=False)
    op.create_index(
        op.f("ix_chat_feedback_updated_at"), "chat_feedback", ["updated_at"], unique=False
    )
    op.create_index(op.f("ix_chat_feedback_rating"), "chat_feedback", ["rating"], unique=False)
    op.create_index(
        op.f("ix_chat_feedback_assistant_message_id"),
        "chat_feedback",
        ["assistant_message_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_chat_feedback_notification_status"),
        "chat_feedback",
        ["notification_status"],
        unique=False,
    )
    op.create_index(op.f("ix_chat_feedback_reason"), "chat_feedback", ["reason"], unique=False)
    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.String(length=None), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repositories.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
    )
    op.create_index(
        op.f("ix_memory_candidates_repo_id"), "memory_candidates", ["repo_id"], unique=False
    )
    op.create_index(
        op.f("ix_memory_candidates_created_at"), "memory_candidates", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_memory_candidates_kind"), "memory_candidates", ["kind"], unique=False)
    op.create_index(
        op.f("ix_memory_candidates_session_id"), "memory_candidates", ["session_id"], unique=False
    )
    op.create_index(
        op.f("ix_memory_candidates_status"), "memory_candidates", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_memory_candidates_conversation_id"),
        "memory_candidates",
        ["conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_memory_candidates_repo_id"), table_name="memory_candidates")
    op.drop_index(op.f("ix_memory_candidates_created_at"), table_name="memory_candidates")
    op.drop_index(op.f("ix_memory_candidates_kind"), table_name="memory_candidates")
    op.drop_index(op.f("ix_memory_candidates_session_id"), table_name="memory_candidates")
    op.drop_index(op.f("ix_memory_candidates_status"), table_name="memory_candidates")
    op.drop_index(op.f("ix_memory_candidates_conversation_id"), table_name="memory_candidates")
    op.drop_table("memory_candidates")
    op.drop_index(op.f("ix_chat_feedback_user_id"), table_name="chat_feedback")
    op.drop_index(op.f("ix_chat_feedback_conversation_id"), table_name="chat_feedback")
    op.drop_index(op.f("ix_chat_feedback_repo_id"), table_name="chat_feedback")
    op.drop_index(op.f("ix_chat_feedback_review_status"), table_name="chat_feedback")
    op.drop_index(op.f("ix_chat_feedback_created_at"), table_name="chat_feedback")
    op.drop_index(op.f("ix_chat_feedback_run_id"), table_name="chat_feedback")
    op.drop_index(op.f("ix_chat_feedback_updated_at"), table_name="chat_feedback")
    op.drop_index(op.f("ix_chat_feedback_rating"), table_name="chat_feedback")
    op.drop_index(op.f("ix_chat_feedback_assistant_message_id"), table_name="chat_feedback")
    op.drop_index(op.f("ix_chat_feedback_notification_status"), table_name="chat_feedback")
    op.drop_index(op.f("ix_chat_feedback_reason"), table_name="chat_feedback")
    op.drop_table("chat_feedback")
    op.drop_index(op.f("ix_agent_task_runs_task_id"), table_name="agent_task_runs")
    op.drop_index(op.f("ix_agent_task_runs_agent_name"), table_name="agent_task_runs")
    op.drop_index(op.f("ix_agent_task_runs_status"), table_name="agent_task_runs")
    op.drop_index(op.f("ix_agent_task_runs_task_type"), table_name="agent_task_runs")
    op.drop_index(op.f("ix_agent_task_runs_workflow_id"), table_name="agent_task_runs")
    op.drop_table("agent_task_runs")
    op.drop_index(op.f("ix_recall_events_created_at"), table_name="recall_events")
    op.drop_index(op.f("ix_recall_events_repo_id"), table_name="recall_events")
    op.drop_index(op.f("ix_recall_events_status"), table_name="recall_events")
    op.drop_index(op.f("ix_recall_events_tool_name"), table_name="recall_events")
    op.drop_index(op.f("ix_recall_events_conversation_id"), table_name="recall_events")
    op.drop_table("recall_events")
    op.drop_index(op.f("ix_pr_review_comments_pr_id"), table_name="pr_review_comments")
    op.drop_table("pr_review_comments")
    op.drop_index(op.f("ix_pr_files_pr_id"), table_name="pr_files")
    op.drop_table("pr_files")
    op.drop_index(op.f("ix_evidence_items_source_type"), table_name="evidence_items")
    op.drop_index(op.f("ix_evidence_items_conversation_id"), table_name="evidence_items")
    op.drop_index(op.f("ix_evidence_items_repo_id"), table_name="evidence_items")
    op.drop_index(op.f("ix_evidence_items_source_id"), table_name="evidence_items")
    op.drop_index(op.f("ix_evidence_items_created_at"), table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_index(op.f("ix_conversation_memories_repo_id"), table_name="conversation_memories")
    op.drop_index(
        op.f("ix_conversation_memories_conversation_id"), table_name="conversation_memories"
    )
    op.drop_table("conversation_memories")
    op.drop_index(op.f("ix_code_relations_relation_type"), table_name="code_relations")
    op.drop_index(op.f("ix_code_relations_repo_id"), table_name="code_relations")
    op.drop_index(op.f("ix_code_relations_pr_number"), table_name="code_relations")
    op.drop_index(op.f("ix_code_relations_path"), table_name="code_relations")
    op.drop_index(op.f("ix_code_relations_target_symbol_id"), table_name="code_relations")
    op.drop_index(op.f("ix_code_relations_source_name"), table_name="code_relations")
    op.drop_index(op.f("ix_code_relations_branch"), table_name="code_relations")
    op.drop_index(op.f("ix_code_relations_created_at"), table_name="code_relations")
    op.drop_index(op.f("ix_code_relations_target_name"), table_name="code_relations")
    op.drop_index(op.f("ix_code_relations_pr_id"), table_name="code_relations")
    op.drop_index(op.f("ix_code_relations_source_symbol_id"), table_name="code_relations")
    op.drop_table("code_relations")
    op.drop_index(op.f("ix_chat_sessions_user_message_id"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_created_at"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_status"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_conversation_id"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_assistant_message_id"), table_name="chat_sessions")
    op.drop_index(op.f("ix_chat_sessions_repo_id"), table_name="chat_sessions")
    op.drop_table("chat_sessions")
    op.drop_index(op.f("ix_chat_messages_created_at"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_repo_id"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_role"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_conversation_id"), table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index(op.f("ix_agent_workflow_runs_conversation_id"), table_name="agent_workflow_runs")
    op.drop_index(op.f("ix_agent_workflow_runs_created_at"), table_name="agent_workflow_runs")
    op.drop_index(op.f("ix_agent_workflow_runs_repo_id"), table_name="agent_workflow_runs")
    op.drop_index(op.f("ix_agent_workflow_runs_status"), table_name="agent_workflow_runs")
    op.drop_table("agent_workflow_runs")
    op.drop_index(op.f("ix_agent_runs_conversation_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index(op.f("ix_workflow_runs_repo_id"), table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index(op.f("ix_thread_memories_repo_id"), table_name="thread_memories")
    op.drop_table("thread_memories")
    op.drop_index(op.f("ix_retrieval_test_runs_repo_id"), table_name="retrieval_test_runs")
    op.drop_index(op.f("ix_retrieval_test_runs_created_at"), table_name="retrieval_test_runs")
    op.drop_table("retrieval_test_runs")
    op.drop_index(op.f("ix_pull_requests_repo_id"), table_name="pull_requests")
    op.drop_index(op.f("ix_pull_requests_number"), table_name="pull_requests")
    op.drop_table("pull_requests")
    op.drop_index(op.f("ix_project_index_states_repo_id"), table_name="project_index_states")
    op.drop_index(op.f("ix_project_index_states_status"), table_name="project_index_states")
    op.drop_table("project_index_states")
    op.drop_index(
        op.f("ix_knowledge_source_documents_status"), table_name="knowledge_source_documents"
    )
    op.drop_index(
        op.f("ix_knowledge_source_documents_created_at"), table_name="knowledge_source_documents"
    )
    op.drop_index(
        op.f("ix_knowledge_source_documents_checksum"), table_name="knowledge_source_documents"
    )
    op.drop_index(
        op.f("ix_knowledge_source_documents_repo_id"), table_name="knowledge_source_documents"
    )
    op.drop_table("knowledge_source_documents")
    op.drop_index(op.f("ix_knowledge_graph_edges_repo_id"), table_name="knowledge_graph_edges")
    op.drop_index(op.f("ix_knowledge_graph_edges_created_at"), table_name="knowledge_graph_edges")
    op.drop_index(op.f("ix_knowledge_graph_edges_to_id"), table_name="knowledge_graph_edges")
    op.drop_index(op.f("ix_knowledge_graph_edges_relation"), table_name="knowledge_graph_edges")
    op.drop_index(op.f("ix_knowledge_graph_edges_from_id"), table_name="knowledge_graph_edges")
    op.drop_index(op.f("ix_knowledge_graph_edges_from_type"), table_name="knowledge_graph_edges")
    op.drop_index(op.f("ix_knowledge_graph_edges_to_type"), table_name="knowledge_graph_edges")
    op.drop_table("knowledge_graph_edges")
    op.drop_index(op.f("ix_knowledge_base_configs_repo_id"), table_name="knowledge_base_configs")
    op.drop_table("knowledge_base_configs")
    op.drop_index(op.f("ix_issues_number"), table_name="issues")
    op.drop_index(op.f("ix_issues_repo_id"), table_name="issues")
    op.drop_table("issues")
    op.drop_index(op.f("ix_documents_source_type"), table_name="documents")
    op.drop_index(op.f("ix_documents_source_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_repo_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_index(op.f("ix_conversations_status"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_updated_at"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_repo_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_created_at"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_index(op.f("ix_code_symbols_commit_sha"), table_name="code_symbols")
    op.drop_index(op.f("ix_code_symbols_created_at"), table_name="code_symbols")
    op.drop_index(op.f("ix_code_symbols_kind"), table_name="code_symbols")
    op.drop_index(op.f("ix_code_symbols_path"), table_name="code_symbols")
    op.drop_index(op.f("ix_code_symbols_pr_id"), table_name="code_symbols")
    op.drop_index(op.f("ix_code_symbols_language"), table_name="code_symbols")
    op.drop_index(op.f("ix_code_symbols_repo_id"), table_name="code_symbols")
    op.drop_index(op.f("ix_code_symbols_pr_number"), table_name="code_symbols")
    op.drop_index(op.f("ix_code_symbols_name"), table_name="code_symbols")
    op.drop_index(op.f("ix_code_symbols_branch"), table_name="code_symbols")
    op.drop_table("code_symbols")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_target_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_user_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_repo_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_target_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_status"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_action_drafts_target_type"), table_name="action_drafts")
    op.drop_index(op.f("ix_action_drafts_status"), table_name="action_drafts")
    op.drop_index(op.f("ix_action_drafts_confirmed_by"), table_name="action_drafts")
    op.drop_index(op.f("ix_action_drafts_created_at"), table_name="action_drafts")
    op.drop_index(op.f("ix_action_drafts_created_by"), table_name="action_drafts")
    op.drop_index(op.f("ix_action_drafts_target_id"), table_name="action_drafts")
    op.drop_index(op.f("ix_action_drafts_draft_type"), table_name="action_drafts")
    op.drop_index(op.f("ix_action_drafts_risk_level"), table_name="action_drafts")
    op.drop_index(op.f("ix_action_drafts_repo_id"), table_name="action_drafts")
    op.drop_table("action_drafts")
    op.drop_index(op.f("ix_workspaces_name"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_created_by"), table_name="workspaces")
    op.drop_index(op.f("ix_workspaces_created_at"), table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_index(op.f("ix_repositories_name"), table_name="repositories")
    op.drop_index(op.f("ix_repositories_full_name"), table_name="repositories")
    op.drop_index(op.f("ix_repositories_owner"), table_name="repositories")
    op.drop_table("repositories")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_table("users")
    op.drop_table("eval_runs")
    op.drop_index(op.f("ix_analysis_results_analysis_type"), table_name="analysis_results")
    op.drop_index(op.f("ix_analysis_results_target_id"), table_name="analysis_results")
    op.drop_index(op.f("ix_analysis_results_target_type"), table_name="analysis_results")
    op.drop_table("analysis_results")
