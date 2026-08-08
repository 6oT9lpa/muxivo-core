from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from src.domain.dataset.dataset_source import DatasetSource
from src.domain.decision.moderation_decision import ModerationDecision
from src.domain.dto.action.action_execution_plan_result import ActionExecutionPlanResult
from src.domain.dto.dataset.dataset_feedback_label import DatasetFeedbackLabel
from src.domain.message_context import MessageContext
from src.domain.rules.rule_evaluation_result import RuleEvaluationResult


class DatasetCollectionInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    context: MessageContext
    rule_evaluation: RuleEvaluationResult
    decision: ModerationDecision
    action_result: ActionExecutionPlanResult | None = None
    source: DatasetSource | None = None
    feedback: DatasetFeedbackLabel | None = None
    store_raw_text: bool = False
    correlation_id: str | None = None
