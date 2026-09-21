"""Add all confirmed entities (docs/03 §3.1, v1.1 §7).

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-21

This migration creates the 17 confirmed tables, the settings singleton row (Test Mode ON by
default, id=1) and the seed-time indexing/unique constraints defined in docs/03. Enum-ish
fields are stored as constrained VARCHARs (cross-dialect); values are validated in code.
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("listing_url", sa.String(length=1024), nullable=True),
        sa.Column("parser_config", sa.JSON(), nullable=True),
        sa.Column("crawl_frequency_minutes", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("auth_encrypted", sa.Text(), nullable=True),
        sa.Column("expected_languages", sa.JSON(), nullable=True),
        sa.Column("recipient_scope", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_sources_name"),
    )

    op.create_table(
        "tenders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=1024), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_timezone", sa.String(length=64), nullable=True),
        sa.Column("raw_metadata", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("is_update", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_tenders_source_external"),
        sa.UniqueConstraint("correlation_id", name="uq_tenders_correlation_id"),
    )
    op.create_index("ix_tenders_source_id", "tenders", ["source_id"])
    op.create_index("ix_tenders_status", "tenders", ["status"])
    op.create_index("ix_tenders_deadline", "tenders", ["deadline"])
    op.create_index("ix_tenders_correlation_id", "tenders", ["correlation_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tender_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=1024), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("extracted_text_ref", sa.Text(), nullable=True),
        sa.Column("download_status", sa.String(length=24), nullable=False),
        sa.Column("extraction_status", sa.String(length=24), nullable=False),
        sa.Column("extraction_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tender_id", "checksum", name="uq_documents_tender_checksum"),
    )
    op.create_index("ix_documents_tender_id", "documents", ["tender_id"])
    op.create_index("ix_documents_checksum", "documents", ["checksum"])
    op.create_index("ix_documents_download_status", "documents", ["download_status"])
    op.create_index("ix_documents_extraction_status", "documents", ["extraction_status"])

    op.create_table(
        "knowledge_base_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_ref", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_kb_versions_content_hash", "knowledge_base_versions", ["content_hash"], unique=True
    )
    op.create_index("ix_kb_versions_id", "knowledge_base_versions", ["id"])

    op.create_table(
        "llm_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("context_window_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("extra_headers", sa.JSON(), nullable=True),
        sa.Column("supports_json", sa.Boolean(), nullable=False),
        sa.Column("supports_vision", sa.Boolean(), nullable=False),
        sa.Column("cost_per_1k_input", sa.Float(), nullable=True),
        sa.Column("cost_per_1k_output", sa.Float(), nullable=True),
        sa.Column("approved_for_company_docs", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_llm_profiles_name"),
    )

    op.create_table(
        "llm_role_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("fallback_profile_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["fallback_profile_id"], ["llm_profiles.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["llm_profiles.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("role", name="uq_llm_role_assignments_role"),
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("est_cost", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["llm_profiles.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_llm_calls_correlation_id", "llm_calls", ["correlation_id"])

    op.create_table(
        "verdicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tender_id", sa.Integer(), nullable=False),
        sa.Column("recommendation", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("background_summary", sa.Text(), nullable=True),
        sa.Column("requirements_summary", sa.JSON(), nullable=True),
        sa.Column("gap_analysis", sa.JSON(), nullable=True),
        sa.Column("urgency_flag", sa.Boolean(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("llm_profile_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("knowledge_base_version_id", sa.Integer(), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("incomplete_inputs", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["llm_profile_id"], ["llm_profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_version_id"], ["knowledge_base_versions.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_verdicts_tender_id", "verdicts", ["tender_id"])
    op.create_index("ix_verdicts_recommendation", "verdicts", ["recommendation"])

    op.create_table(
        "mail_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("provider_type", sa.String(length=64), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=True),
        sa.Column("from_address", sa.String(length=255), nullable=False),
        sa.Column("from_name", sa.String(length=255), nullable=True),
        sa.Column("reply_to", sa.String(length=255), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("breaker_state", sa.String(length=24), nullable=False),
        sa.Column("breaker_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_mail_providers_name"),
    )

    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tender_id", sa.Integer(), nullable=True),
        sa.Column("verdict_id", sa.Integer(), nullable=True),
        sa.Column("recipients_snapshot", sa.JSON(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attachments", sa.JSON(), nullable=True),
        sa.Column("links", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider_used", sa.String(length=255), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("possible_duplicate", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["verdict_id"], ["verdicts.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_notification_logs_sent_at", "notification_logs", ["sent_at"])
    op.create_index("ix_notification_logs_status", "notification_logs", ["status"])

    op.create_table(
        "notification_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("notification_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["notification_id"], ["notification_logs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["provider_id"], ["mail_providers.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_notification_attempts_notification_id", "notification_attempts", ["notification_id"])
    op.create_index("ix_notification_attempts_status", "notification_attempts", ["status"])

    op.create_table(
        "recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=255), nullable=True),
        sa.Column("list_type", sa.String(length=24), nullable=False),
        sa.Column("delivery", sa.String(length=16), nullable=False),
        sa.Column("source_scope", sa.JSON(), nullable=True),
        sa.Column("receives_filter", sa.String(length=24), nullable=False),
        sa.Column("alert_types", sa.JSON(), nullable=True),
        sa.Column("min_severity", sa.String(length=24), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recipients_list_type", "recipients", ["list_type"])
    op.create_index("ix_recipients_active", "recipients", ["active"])

    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=24), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.String(length=36), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("first_raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reminded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_alert_events_state", "alert_events", ["state"])

    op.create_table(
        "run_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("listings_found", sa.Integer(), nullable=False),
        sa.Column("new_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("failed_correlation_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_run_history_source_id", "run_history", ["source_id"])
    op.create_index("ix_run_history_started_at", "run_history", ["started_at"])

    op.create_table(
        "settings_singleton",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("test_mode", sa.Boolean(), nullable=False),
        sa.Column("test_mode_reason", sa.String(length=1024), nullable=True),
        sa.Column("test_mode_enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("test_mode_enabled_by", sa.String(length=255), nullable=True),
        sa.Column("urgency_window_days", sa.Integer(), nullable=False),
        sa.Column("triage_threshold", sa.Float(), nullable=True),
        sa.Column("triage_rules", sa.JSON(), nullable=True),
        sa.Column("monthly_ai_budget", sa.Float(), nullable=True),
        sa.Column("alert_thresholds", sa.JSON(), nullable=True),
        sa.Column("retention_months", sa.Integer(), nullable=False),
        sa.Column("link_expiry_days", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "config_change_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("entity", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("changed_fields", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_admin_users_email"),
    )

    seed_defaults = sa.table(
        "settings_singleton",
        sa.column("id", sa.Integer()),
        sa.column("test_mode", sa.Boolean()),
        sa.column("test_mode_reason", sa.String()),
        sa.column("urgency_window_days", sa.Integer()),
        sa.column("triage_threshold", sa.Float()),
        sa.column("monthly_ai_budget", sa.Float()),
        sa.column("retention_months", sa.Integer()),
        sa.column("link_expiry_days", sa.Integer()),
        sa.column("version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    _now = datetime.now(timezone.utc)
    op.bulk_insert(
        seed_defaults,
        [
            {
                "id": 1,
                "test_mode": True,
                "test_mode_reason": "seeded default: test mode on",
                "urgency_window_days": 5,
                "triage_threshold": None,
                "monthly_ai_budget": None,
                "retention_months": 12,
                "link_expiry_days": 14,
                "version": 1,
                "created_at": _now,
                "updated_at": _now,
            }
        ],
    )


def downgrade() -> None:
    for table in (
        "admin_users",
        "config_change_log",
        "settings_singleton",
        "run_history",
        "alert_events",
        "recipients",
        "notification_attempts",
        "notification_logs",
        "mail_providers",
        "verdicts",
        "llm_calls",
        "llm_role_assignments",
        "llm_profiles",
        "knowledge_base_versions",
        "documents",
        "tenders",
        "sources",
    ):
        op.drop_table(table)