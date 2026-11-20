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
}


def merged_settings(overrides: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(DEFAULT_STATION_SETTINGS)
    if overrides:
        result.update({k: v for k, v in overrides.items() if k in DEFAULT_STATION_SETTINGS})
    return result
