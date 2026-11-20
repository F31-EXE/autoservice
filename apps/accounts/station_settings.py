"""Per-station settings stored in ``ServiceStation.settings`` (spec section 7.3)."""

from typing import Any

DEFAULT_STATION_SETTINGS: dict[str, Any] = {
    "remind_before_days": 14,
    "merge_window_days": 30,
    "max_touches": 2,
    "second_touch_after_days": 3,
    "send_window": ["10:00", "20:00"],
    "inactive_vehicle_days": 730,
    "control_group_share": 0.12,
    "attribution_window_days": 30,
    "high_value_call_threshold": 15000,
    "recommendation_ttl_days": 540,
    "llm_compose_enabled": False,
    # after a skipped / cancelled / unanswered reminder, don't queue the vehicle again for N days
    "skip_cooldown_days": 30,
    # message templates (Django template syntax); empty = built-in defaults
    "message_template": "",  # MAX (messenger)
    "sms_template": "",  # SMS, must stay short
    # LLM extraction skips work orders older than this (None = recommendation_ttl_days)
    "llm_extract_max_age_days": None,
}


def merged_settings(overrides: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(DEFAULT_STATION_SETTINGS)
    if overrides:
        result.update({k: v for k, v in overrides.items() if k in DEFAULT_STATION_SETTINGS})
    return result
