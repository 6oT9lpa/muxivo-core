from src.application.api_moderation_service import ApiModerationService
from src.domain.moderation.moderation_action import ModerationAction


def test_action_result_accepts_the_actual_policy_enforced_action() -> None:
    service = object.__new__(ApiModerationService)

    assert service._allowed_execution_actions(ModerationAction.DELETE) == tuple(ModerationAction)
    assert service._allowed_execution_actions(ModerationAction.BAN) == tuple(ModerationAction)


def test_action_result_accepts_shadow_mode_logging_for_a_high_risk_recommendation() -> None:
    service = object.__new__(ApiModerationService)

    assert ModerationAction.LOG in service._allowed_execution_actions(ModerationAction.DELETE_WARN)
