from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

_SECRET_KEYS = re.compile(r"(token|secret|password|credential|authorization)", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def redact(value: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove known secret values and common key/value secret formats from text."""
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = re.sub(
        r"(?im)\b(token|secret|password|credential|authorization)\s*([=:])\s*\S+",
        r"\1\2[REDACTED]",
        result,
    )
    return result


def redact_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEYS.search(key) else redact_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


@dataclass
class DrillReport:
    status: str
    stage: str
    started_at: str
    duration_seconds: float = 0.0
    k3s_version: str | None = None
    snapshot_size_bytes: int | None = None
    api_ready: bool = False
    marker_found: bool = False
    error_code: str | None = None
    hint: str | None = None
    stages: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def finish(self, *, status: str, stage: str, started: datetime, **fields: Any) -> None:
        self.status = status
        self.stage = stage
        self.duration_seconds = round((datetime.now(UTC) - started).total_seconds(), 3)
        for key, value in fields.items():
            setattr(self, key, value)

    def as_dict(self) -> dict[str, Any]:
        return redact_data(asdict(self))

    def write(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            destination.chmod(0o600)
        except OSError:
            pass
