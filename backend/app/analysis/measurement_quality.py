"""Conservative, JSON-safe quality metadata shared by archives and integrations."""
from typing import Any, Mapping


def measurement_quality(source: Mapping[str, Any]) -> dict[str, Any]:
    raw = source.get("quality_issues") or []
    if isinstance(raw, str):
        raw = [raw]
    issues = sorted({str(issue) for issue in raw if issue})
    def captured_encoder_field(field: str) -> str:
        captured = f"measurement_{field}"
        key = captured if not source.get("recording") and captured in source else field
        return str(source.get(key) or "")

    encoder_error = captured_encoder_field("encoder_error")
    encoder_error_message = captured_encoder_field("encoder_error_message")
    valid = source.get("measurement_valid")
    if issues or encoder_error or encoder_error_message:
        status = "invalid"
    elif source.get("recording") or source.get("analysis_pending"):
        status = "unknown"
    elif source.get("quality_status") == "invalid" or (valid is False and source.get("quality_status") != "unknown"):
        status = "invalid"
    elif valid is True:
        status = "valid"
    else:
        status = "unknown"
    return {
        "measurement_valid": status == "valid",
        "quality_status": status,
        "quality_issues": issues,
        "encoder_error": encoder_error,
        "encoder_error_message": encoder_error_message,
    }
