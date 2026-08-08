from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from src.domain.action.action_execution_status import ActionExecutionStatus
from src.domain.dataset.dataset_collector_repository import DatasetCollectorRepository
from src.domain.dto.dataset.dataset_collection_record import DatasetCollectionRecord
from src.domain.dto.dataset.dataset_collection_result import DatasetCollectionResult
from src.infrastructure.database.connection import DatabaseConnection, DatabaseSession
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


class PostgresqlDatasetCollectorRepository(DatasetCollectorRepository):
    def __init__(self, database: DatabaseConnection) -> None:
        self._database = database

    async def save_collection(
        self,
        record: DatasetCollectionRecord,
    ) -> DatasetCollectionResult:
        async with self._database.transaction() as transaction:
            await self._purge_expired_collections(transaction)
            event_id = await self._upsert_event(transaction, record)
            await self._upsert_features(transaction, event_id, record)
            await self._insert_rule_analysis(transaction, event_id, record)
            decision_id = await self._insert_decision(transaction, event_id, record)

            if record.feedback is not None:
                await self._insert_feedback(transaction, event_id, decision_id, record)

        training_example = record.training_example.model_copy(
            update={"event_id": event_id}
        )
        logger.info(
            "Dataset collection persisted event_id=%s decision_id=%s message_id=%s",
            event_id,
            decision_id,
            record.message_id,
        )
        return DatasetCollectionResult(
            event_id=event_id,
            decision_id=decision_id,
            training_example=training_example,
        )

    async def _purge_expired_collections(self, database: DatabaseSession) -> None:
        await database.execute(
            "DELETE FROM ai_message_events WHERE retention_until IS NOT NULL AND retention_until <= CURRENT_TIMESTAMP"
        )

    async def _upsert_event(
        self, database: DatabaseSession, record: DatasetCollectionRecord
    ) -> int:
        result = await database.execute(
            """
            INSERT INTO ai_message_events (
                platform,
                message_id,
                guild_id,
                channel_id,
                user_id,
                event_type,
                source,
                raw_text,
                normalized_text,
                text_hash,
                language,
                reply_to_message_id,
                has_attachments,
                attachment_count,
                created_at,
                processed_at,
                retention_until,
                correlation_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (guild_id, message_id) DO UPDATE SET
                platform = EXCLUDED.platform,
                channel_id = EXCLUDED.channel_id,
                user_id = EXCLUDED.user_id,
                event_type = EXCLUDED.event_type,
                source = EXCLUDED.source,
                raw_text = EXCLUDED.raw_text,
                normalized_text = EXCLUDED.normalized_text,
                text_hash = EXCLUDED.text_hash,
                language = EXCLUDED.language,
                reply_to_message_id = EXCLUDED.reply_to_message_id,
                has_attachments = EXCLUDED.has_attachments,
                attachment_count = EXCLUDED.attachment_count,
                processed_at = EXCLUDED.processed_at,
                retention_until = EXCLUDED.retention_until,
                correlation_id = COALESCE(EXCLUDED.correlation_id, ai_message_events.correlation_id)
            RETURNING id
            """,
            [
                record.platform,
                record.message_id,
                record.guild_id,
                record.channel_id,
                record.user_id,
                record.event_type,
                record.source.value,
                record.text.raw_text,
                record.text.normalized_text,
                record.text.text_hash,
                record.language,
                record.reply_to_message_id,
                record.has_attachments,
                record.attachment_count,
                record.created_at,
                record.processed_at,
                record.retention_until,
                record.correlation_id,
            ],
        )
        if result.lastrowid is None:
            raise RuntimeError(
                f"Dataset event was not returned message_id={record.message_id}"
            )

        return result.lastrowid

    async def _upsert_features(
        self,
        database: DatabaseSession,
        event_id: int,
        record: DatasetCollectionRecord,
    ) -> None:
        features = record.features
        await database.execute(
            """
            INSERT INTO ai_message_features (
                event_id,
                char_count,
                token_count,
                word_count,
                line_count,
                url_count,
                invite_count,
                mention_count,
                role_mention_count,
                channel_mention_count,
                emoji_count,
                emoji_ratio,
                caps_ratio,
                repeated_char_score,
                duplicate_text_score,
                has_url,
                has_invite,
                has_shortener,
                has_mixed_scripts,
                has_zero_width,
                has_suspicious_unicode,
                is_reply,
                account_age_days,
                member_age_days,
                recent_user_messages_10s,
                recent_user_messages_60s,
                recent_user_messages_10m,
                repeated_messages_10m,
                features_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO UPDATE SET
                char_count = EXCLUDED.char_count,
                token_count = EXCLUDED.token_count,
                word_count = EXCLUDED.word_count,
                line_count = EXCLUDED.line_count,
                url_count = EXCLUDED.url_count,
                invite_count = EXCLUDED.invite_count,
                mention_count = EXCLUDED.mention_count,
                role_mention_count = EXCLUDED.role_mention_count,
                channel_mention_count = EXCLUDED.channel_mention_count,
                emoji_count = EXCLUDED.emoji_count,
                emoji_ratio = EXCLUDED.emoji_ratio,
                caps_ratio = EXCLUDED.caps_ratio,
                repeated_char_score = EXCLUDED.repeated_char_score,
                duplicate_text_score = EXCLUDED.duplicate_text_score,
                has_url = EXCLUDED.has_url,
                has_invite = EXCLUDED.has_invite,
                has_shortener = EXCLUDED.has_shortener,
                has_mixed_scripts = EXCLUDED.has_mixed_scripts,
                has_zero_width = EXCLUDED.has_zero_width,
                has_suspicious_unicode = EXCLUDED.has_suspicious_unicode,
                is_reply = EXCLUDED.is_reply,
                account_age_days = EXCLUDED.account_age_days,
                member_age_days = EXCLUDED.member_age_days,
                recent_user_messages_10s = EXCLUDED.recent_user_messages_10s,
                recent_user_messages_60s = EXCLUDED.recent_user_messages_60s,
                recent_user_messages_10m = EXCLUDED.recent_user_messages_10m,
                repeated_messages_10m = EXCLUDED.repeated_messages_10m,
                features_json = EXCLUDED.features_json
            """,
            [
                event_id,
                features.get("text_length", 0),
                features.get("token_count", 0),
                features.get("word_count", 0),
                features.get("line_count", 0),
                features.get("url_count", 0),
                features.get("invite_count", 0),
                features.get("mention_count", 0),
                features.get("role_mention_count", 0),
                features.get("channel_mention_count", 0),
                features.get("emoji_count", 0),
                features.get("emoji_ratio", 0),
                features.get("uppercase_ratio", 0),
                features.get("repeated_char_score", 0),
                features.get("duplicate_text_score", 0),
                features.get("has_url", False),
                features.get("has_invite", False),
                features.get("has_shortener", False),
                features.get("has_mixed_scripts", False),
                features.get("has_zero_width", False),
                features.get("has_suspicious_unicode", False),
                record.reply_to_message_id is not None,
                features.get("account_age_days"),
                features.get("member_age_days"),
                features.get("recent_user_messages_10s", 0),
                features.get("recent_user_messages_60s", 0),
                features.get("recent_user_messages_10m", 0),
                features.get("repeated_messages_10m", 0),
                Jsonb(
                    {
                        **features,
                        **record.metadata,
                        "training_example": record.training_example.model_dump(
                            mode="json"
                        ),
                    }
                ),
            ],
        )

    async def _insert_rule_analysis(
        self,
        database: DatabaseSession,
        event_id: int,
        record: DatasetCollectionRecord,
    ) -> None:
        rule_result = record.rule_evaluation
        await database.execute(
            """
            INSERT INTO ai_analysis_results (
                event_id,
                attachment_id,
                stage,
                model_name,
                model_version,
                input_version,
                policy_version,
                output_json,
                label,
                labels_json,
                confidence,
                probabilities_json,
                rule_matches_json,
                risk_score,
                risk_breakdown_json,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (
                event_id, attachment_id, stage, model_name, model_version,
                input_version, policy_version
            ) DO UPDATE SET
                output_json = EXCLUDED.output_json,
                label = EXCLUDED.label,
                labels_json = EXCLUDED.labels_json,
                confidence = EXCLUDED.confidence,
                probabilities_json = EXCLUDED.probabilities_json,
                rule_matches_json = EXCLUDED.rule_matches_json,
                risk_score = EXCLUDED.risk_score,
                risk_breakdown_json = EXCLUDED.risk_breakdown_json,
                created_at = EXCLUDED.created_at
            """,
            [
                event_id,
                "__message__",
                "rule_engine",
                "rule_engine",
                rule_result.policy_version,
                rule_result.policy_id,
                rule_result.policy_version,
                Jsonb(rule_result.model_dump(mode="json")),
                rule_result.primary_label.value,
                Jsonb([label.value for label in rule_result.labels]),
                rule_result.confidence,
                Jsonb({}),
                Jsonb(rule_result.matched_rules),
                round(rule_result.risk_score),
                Jsonb(
                    [
                        item.model_dump(mode="json")
                        for item in rule_result.risk_breakdown
                    ]
                ),
                rule_result.created_at,
            ],
        )

    async def _insert_decision(
        self,
        database: DatabaseSession,
        event_id: int,
        record: DatasetCollectionRecord,
    ) -> int | None:
        action_status = record.action_result.status if record.action_result else None
        action_success = self._is_action_success(action_status)
        result = await database.execute(
            """
            INSERT INTO ai_moderation_decisions (
                event_id,
                policy_version,
                decision_action,
                severity,
                reason_code,
                reason_text,
                action_taken,
                action_success,
                platform_error,
                review_status,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id, policy_version, decision_action, created_at)
            DO UPDATE SET
                severity = EXCLUDED.severity,
                reason_code = EXCLUDED.reason_code,
                reason_text = EXCLUDED.reason_text,
                action_taken = EXCLUDED.action_taken,
                action_success = EXCLUDED.action_success,
                platform_error = EXCLUDED.platform_error,
                review_status = EXCLUDED.review_status
            RETURNING id
            """,
            [
                event_id,
                record.decision.policy_version,
                record.decision.decision_action.value,
                record.decision.severity,
                record.decision.reason,
                record.decision.reason,
                record.action_result is not None,
                action_success,
                self._extract_platform_error(record),
                self._review_status(record),
                record.decision.created_at,
            ],
        )
        return result.lastrowid

    async def _insert_feedback(
        self,
        database: DatabaseSession,
        event_id: int,
        decision_id: int | None,
        record: DatasetCollectionRecord,
    ) -> None:
        feedback = record.feedback
        if feedback is None:
            return

        await database.execute(
            """
            INSERT INTO ai_feedback_labels (
                event_id,
                decision_id,
                labels_json,
                primary_label,
                scam_subtype,
                severity,
                recommended_action,
                moderator_id,
                feedback_type,
                is_false_positive,
                is_false_negative,
                needs_context,
                annotator_confidence,
                annotation_source,
                notes,
                idempotency_key,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
            """,
            [
                event_id,
                decision_id,
                Jsonb([label.value for label in feedback.labels]),
                feedback.primary_label.value if feedback.primary_label else None,
                feedback.scam_subtype,
                feedback.severity,
                (
                    feedback.recommended_action.value
                    if feedback.recommended_action
                    else None
                ),
                feedback.moderator_id,
                feedback.feedback_type.value,
                feedback.is_false_positive,
                feedback.is_false_negative,
                feedback.needs_context,
                feedback.annotator_confidence,
                feedback.annotation_source,
                feedback.notes,
                f"dataset:{event_id}:{decision_id}:{feedback.annotation_source}:{feedback.feedback_type.value}",
            ],
        )

    def _is_action_success(self, status: ActionExecutionStatus | None) -> bool | None:
        if status is None:
            return None

        return status in {
            ActionExecutionStatus.SUCCESS,
            ActionExecutionStatus.DRY_RUN,
            ActionExecutionStatus.PARTIAL_SUCCESS,
            ActionExecutionStatus.SKIPPED,
        }

    def _extract_platform_error(self, record: DatasetCollectionRecord) -> str | None:
        if record.action_result is None:
            return None

        errors = [step.error for step in record.action_result.steps if step.error]
        return "; ".join(errors) if errors else None

    def _review_status(self, record: DatasetCollectionRecord) -> str:
        if record.feedback is not None:
            return record.feedback.feedback_type.value

        if record.decision.decision_action.value == "REVIEW":
            return "pending"

        return "not_required"
