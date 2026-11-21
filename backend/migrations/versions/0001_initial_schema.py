"""0001 initial baseline schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-21 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), server_default="demo-user", nullable=False),
        sa.Column("role", sa.String(80), server_default="owner", nullable=False),
        sa.Column("github_token_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_users_role", "users", ["role"])

    # 2. repositories
    op.create_table(
        "repositories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("full_name", sa.String(400), nullable=False),
        sa.Column("provider", sa.String(80), server_default="github", nullable=False),
        sa.Column("api_base_url", sa.Text(), nullable=True),
        sa.Column("clone_url", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("checkout_mode", sa.String(80), server_default="managed", nullable=False),
        sa.Column("github_token_encrypted", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_branch", sa.String(200), nullable=True),
        sa.Column("github_id", sa.BigInteger(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_repositories_owner", "repositories", ["owner"])
    op.create_index("ix_repositories_name", "repositories", ["name"])
    op.create_index("ix_repositories_full_name", "repositories", ["full_name"], unique=True)

    # 3. workspaces
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("repo_ids", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_workspaces_name", "workspaces", ["name"])
    op.create_index("ix_workspaces_created_by", "workspaces", ["created_by"])

    # 4. project_index_states
    op.create_table(
        "project_index_states",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), unique=True, nullable=False
        ),
        sa.Column("status", sa.String(40), server_default="missing", nullable=False),
        sa.Column("fingerprint", sa.String(200), server_default="", nullable=False),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("docs_indexed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("docs_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_project_index_states_status", "project_index_states", ["status"])

    # 5. conversations
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("title", sa.String(300), server_default="默认会话", nullable=False),
        sa.Column("status", sa.String(80), server_default="active", nullable=False),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_conversations_repo_id", "conversations", ["repo_id"])
    op.create_index("ix_conversations_status", "conversations", ["status"])

    # 6. issues
    op.create_table(
        "issues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("github_issue_id", sa.BigInteger(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("state", sa.String(50), server_default="open", nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("author", sa.String(200), nullable=True),
        sa.Column("assignees", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_issues_repo_id", "issues", ["repo_id"])
    op.create_index("ix_issues_number", "issues", ["number"])

    # 7. pull_requests
    op.create_table(
        "pull_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("github_pr_id", sa.BigInteger(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("state", sa.String(50), server_default="open", nullable=False),
        sa.Column("author", sa.String(200), nullable=True),
        sa.Column("base_branch", sa.String(200), nullable=True),
        sa.Column("head_branch", sa.String(200), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pull_requests_repo_id", "pull_requests", ["repo_id"])
    op.create_index("ix_pull_requests_number", "pull_requests", ["number"])

    # 8. pr_files
    op.create_table(
        "pr_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pr_id", sa.Uuid(), sa.ForeignKey("pull_requests.id"), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("additions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deletions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("patch", sa.Text(), nullable=True),
    )
    op.create_index("ix_pr_files_pr_id", "pr_files", ["pr_id"])

    # 9. pr_review_comments
    op.create_table(
        "pr_review_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pr_id", sa.Uuid(), sa.ForeignKey("pull_requests.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author", sa.String(200), nullable=True),
        sa.Column("path", sa.String(500), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pr_review_comments_pr_id", "pr_review_comments", ["pr_id"])

    # 10. workflow_runs
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("github_run_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("conclusion", sa.String(50), nullable=True),
        sa.Column("html_url", sa.String(500), nullable=True),
        sa.Column("head_branch", sa.String(200), nullable=True),
        sa.Column("head_sha", sa.String(100), nullable=True),
        sa.Column("run_attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("logs_url", sa.String(500), nullable=True),
        sa.Column("jobs", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_workflow_runs_repo_id", "workflow_runs", ["repo_id"])

    # 11. analysis_results
    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("target_id", sa.String(200), nullable=False),
        sa.Column("analysis_type", sa.String(50), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_analysis_results_repo_id", "analysis_results", ["repo_id"])
    op.create_index("ix_analysis_results_target_type", "analysis_results", ["target_type"])
    op.create_index("ix_analysis_results_target_id", "analysis_results", ["target_id"])
    op.create_index("ix_analysis_results_analysis_type", "analysis_results", ["analysis_type"])

    # 12. documents
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=True),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_documents_repo_id", "documents", ["repo_id"])
    op.create_index("ix_documents_source_type", "documents", ["source_type"])
    op.create_index("ix_documents_source_id", "documents", ["source_id"])

    # 13. knowledge_base_configs
    op.create_table(
        "knowledge_base_configs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), unique=True, nullable=False
        ),
        sa.Column("chunk_size", sa.Integer(), server_default="512", nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), server_default="64", nullable=False),
        sa.Column("enable_parent_child", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("retrieval_strategy", sa.String(50), server_default="hybrid", nullable=False),
        sa.Column("rerank_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # 14. knowledge_source_documents
    op.create_table(
        "knowledge_source_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("filename", sa.String(400), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), server_default="ready", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_knowledge_source_documents_repo_id", "knowledge_source_documents", ["repo_id"]
    )

    # 15. retrieval_test_runs
    op.create_table(
        "retrieval_test_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("strategy", sa.String(50), nullable=False),
        sa.Column("rerank_enabled", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("results_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_retrieval_test_runs_repo_id", "retrieval_test_runs", ["repo_id"])

    # 16. code_symbols
    op.create_table(
        "code_symbols",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("pr_id", sa.Uuid(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("symbol_name", sa.String(300), nullable=False),
        sa.Column("symbol_type", sa.String(80), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("docstring", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_code_symbols_repo_id", "code_symbols", ["repo_id"])
    op.create_index("ix_code_symbols_file_path", "code_symbols", ["file_path"])
    op.create_index("ix_code_symbols_symbol_name", "code_symbols", ["symbol_name"])
    op.create_index("ix_code_symbols_symbol_type", "code_symbols", ["symbol_type"])

    # 17. code_relations
    op.create_table(
        "code_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("pr_id", sa.Uuid(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("source_symbol_id", sa.Uuid(), sa.ForeignKey("code_symbols.id"), nullable=False),
        sa.Column("target_symbol_name", sa.String(300), nullable=False),
        sa.Column("relation_type", sa.String(80), nullable=False),
        sa.Column("target_file_path", sa.String(500), nullable=True),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_code_relations_repo_id", "code_relations", ["repo_id"])
    op.create_index("ix_code_relations_source_symbol_id", "code_relations", ["source_symbol_id"])
    op.create_index(
        "ix_code_relations_target_symbol_name", "code_relations", ["target_symbol_name"]
    )
    op.create_index("ix_code_relations_relation_type", "code_relations", ["relation_type"])

    # 18. chat_messages
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_chat_messages_repo_id", "chat_messages", ["repo_id"])
    op.create_index("ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"])

    # 19. chat_sessions
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("route", sa.String(80), server_default="chat", nullable=False),
        sa.Column("transcript_json", sa.JSON(), nullable=False),
        sa.Column("digest", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_chat_sessions_repo_id", "chat_sessions", ["repo_id"])
    op.create_index("ix_chat_sessions_conversation_id", "chat_sessions", ["conversation_id"])

    # 20. thread_memories
    op.create_table(
        "thread_memories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("thread_id", sa.String(200), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("tasks", sa.JSON(), nullable=False),
        sa.Column("user_preferences", sa.JSON(), nullable=False),
        sa.Column("repo_context", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_thread_memories_repo_id", "thread_memories", ["repo_id"])
    op.create_index("ix_thread_memories_thread_id", "thread_memories", ["thread_id"])

    # 21. conversation_memories
    op.create_table(
        "conversation_memories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("tasks", sa.JSON(), nullable=False),
        sa.Column("user_preferences", sa.JSON(), nullable=False),
        sa.Column("repo_context", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_conversation_memories_repo_id", "conversation_memories", ["repo_id"])
    op.create_index(
        "ix_conversation_memories_conversation_id", "conversation_memories", ["conversation_id"]
    )

    # 22. evidence_items
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(80), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_evidence_items_repo_id", "evidence_items", ["repo_id"])
    op.create_index("ix_evidence_items_conversation_id", "evidence_items", ["conversation_id"])
    op.create_index("ix_evidence_items_source_type", "evidence_items", ["source_type"])
    op.create_index("ix_evidence_items_source_id", "evidence_items", ["source_id"])

    # 23. knowledge_graph_edges
    op.create_table(
        "knowledge_graph_edges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=False),
        sa.Column("from_type", sa.String(80), nullable=False),
        sa.Column("from_id", sa.String(200), nullable=False),
        sa.Column("to_type", sa.String(80), nullable=False),
        sa.Column("to_id", sa.String(200), nullable=False),
        sa.Column("relation", sa.String(80), nullable=False),
        sa.Column("weight", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_knowledge_graph_edges_repo_id", "knowledge_graph_edges", ["repo_id"])
    op.create_index("ix_knowledge_graph_edges_from_type", "knowledge_graph_edges", ["from_type"])
    op.create_index("ix_knowledge_graph_edges_from_id", "knowledge_graph_edges", ["from_id"])
    op.create_index("ix_knowledge_graph_edges_to_type", "knowledge_graph_edges", ["to_type"])
    op.create_index("ix_knowledge_graph_edges_to_id", "knowledge_graph_edges", ["to_id"])
    op.create_index("ix_knowledge_graph_edges_relation", "knowledge_graph_edges", ["relation"])

    # 24. recall_events
    op.create_table(
        "recall_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("query", sa.Text(), server_default="", nullable=False),
        sa.Column("result_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("status", sa.String(50), server_default="success", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_recall_events_repo_id", "recall_events", ["repo_id"])
    op.create_index("ix_recall_events_conversation_id", "recall_events", ["conversation_id"])
    op.create_index("ix_recall_events_tool_name", "recall_events", ["tool_name"])

    # 25. memory_candidates
    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), server_default="pending", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_memory_candidates_repo_id", "memory_candidates", ["repo_id"])
    op.create_index(
        "ix_memory_candidates_conversation_id", "memory_candidates", ["conversation_id"]
    )
    op.create_index("ix_memory_candidates_kind", "memory_candidates", ["kind"])
    op.create_index("ix_memory_candidates_status", "memory_candidates", ["status"])

    # 26. agent_runs
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("route", sa.String(80), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("final_answer", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_runs_repo_id", "agent_runs", ["repo_id"])
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_route", "agent_runs", ["route"])

    # 27. chat_feedback
    op.create_table(
        "chat_feedback",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("rating", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("route", sa.String(80), nullable=True),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("comment", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_chat_feedback_repo_id", "chat_feedback", ["repo_id"])
    op.create_index("ix_chat_feedback_conversation_id", "chat_feedback", ["conversation_id"])
    op.create_index("ix_chat_feedback_rating", "chat_feedback", ["rating"])

    # 28. agent_workflow_runs
    op.create_table(
        "agent_workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), server_default="running", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_workflow_runs_repo_id", "agent_workflow_runs", ["repo_id"])
    op.create_index(
        "ix_agent_workflow_runs_conversation_id", "agent_workflow_runs", ["conversation_id"]
    )
    op.create_index("ix_agent_workflow_runs_status", "agent_workflow_runs", ["status"])

    # 29. agent_task_runs
    op.create_table(
        "agent_task_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workflow_id", sa.Uuid(), sa.ForeignKey("agent_workflow_runs.id"), nullable=False
        ),
        sa.Column("task_id", sa.String(100), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("task_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(50), server_default="running", nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_task_runs_workflow_id", "agent_task_runs", ["workflow_id"])
    op.create_index("ix_agent_task_runs_task_id", "agent_task_runs", ["task_id"])
    op.create_index("ix_agent_task_runs_status", "agent_task_runs", ["status"])

    # 30. eval_runs
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("suite_id", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), server_default="running", nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_eval_runs_suite_id", "eval_runs", ["suite_id"])
    op.create_index("ix_eval_runs_target_type", "eval_runs", ["target_type"])
    op.create_index("ix_eval_runs_status", "eval_runs", ["status"])

    # 31. action_drafts
    op.create_table(
        "action_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("draft_type", sa.String(80), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(50), server_default="pending", nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_action_drafts_repo_id", "action_drafts", ["repo_id"])
    op.create_index("ix_action_drafts_draft_type", "action_drafts", ["draft_type"])
    op.create_index("ix_action_drafts_status", "action_drafts", ["status"])
    op.create_index("ix_action_drafts_created_by", "action_drafts", ["created_by"])
    op.create_index("ix_action_drafts_approved_by", "action_drafts", ["approved_by"])

    # 32. audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("repo_id", sa.Uuid(), sa.ForeignKey("repositories.id"), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(80), nullable=True),
        sa.Column("target_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(50), server_default="success", nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_repo_id", "audit_logs", ["repo_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_status", "audit_logs", ["status"])


def downgrade() -> None:
    tables = [
        "audit_logs",
        "action_drafts",
        "eval_runs",
        "agent_task_runs",
        "agent_workflow_runs",
        "chat_feedback",
        "agent_runs",
        "memory_candidates",
        "recall_events",
        "knowledge_graph_edges",
        "evidence_items",
        "conversation_memories",
        "thread_memories",
        "chat_sessions",
        "chat_messages",
        "code_relations",
        "code_symbols",
        "retrieval_test_runs",
        "knowledge_source_documents",
        "knowledge_base_configs",
        "documents",
        "analysis_results",
        "workflow_runs",
        "pr_review_comments",
        "pr_files",
        "pull_requests",
        "issues",
        "conversations",
        "project_index_states",
        "workspaces",
        "repositories",
        "users",
    ]
    for table in tables:
        op.drop_table(table)
