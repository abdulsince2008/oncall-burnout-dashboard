from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class AlertSource(str, Enum):
    PAGERDUTY = "pagerduty"
    OPSGENIE = "opsgenie"


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlertStatus(str, Enum):
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class RawAlert(BaseModel):
    id: str
    source: AlertSource
    title: str
    severity: AlertSeverity
    status: AlertStatus
    created_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    assignee_id: str
    assignee_name: str
    assignee_email: str | None = None
    service_name: str | None = None
    service_id: str | None = None

    @field_validator("created_at", "acknowledged_at", "resolved_at", mode="before")
    @classmethod
    def parse_datetime(cls, v):
        if isinstance(v, str):
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    continue
            raise ValueError(f"Unable to parse datetime: {v}")
        return v


class OnCallShift(BaseModel):
    user_id: str
    user_name: str
    user_email: str | None = None
    start: datetime
    end: datetime
    schedule_name: str | None = None
    schedule_id: str | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def parse_datetime(cls, v):
        if isinstance(v, str):
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    continue
            raise ValueError(f"Unable to parse datetime: {v}")
        return v


class EngineerMetrics(BaseModel):
    user_id: str
    user_name: str
    user_email: str | None = None

    total_alerts: int = 0
    acked_alerts: int = 0
    resolved_alerts: int = 0

    ack_times_minutes: list[float] = Field(default_factory=list)
    resolve_times_minutes: list[float] = Field(default_factory=list)

    median_ack_time: float = 0.0
    p90_ack_time: float = 0.0
    mean_ack_time: float = 0.0

    median_resolve_time: float = 0.0
    p90_resolve_time: float = 0.0
    mean_resolve_time: float = 0.0

    consecutive_oncall_days: int = 0
    max_consecutive_oncall_days: int = 0
    total_oncall_days_in_window: int = 0

    ack_time_trend_slope: float = 0.0
    ack_time_trend_r2: float = 0.0

    burnout_risk_score: float = 0.0
    risk_level: str = "low"

    def compute_ack_stats(self) -> None:
        if not self.ack_times_minutes:
            return
        import numpy as np

        arr = np.array(self.ack_times_minutes)
        self.median_ack_time = float(np.median(arr))
        self.mean_ack_time = float(np.mean(arr))
        self.p90_ack_time = float(np.percentile(arr, 90))

        if len(arr) >= 3:
            x = np.arange(len(arr))
            slope, intercept = np.polyfit(x, arr, 1)
            self.ack_time_trend_slope = float(slope)
            y_pred = slope * x + intercept
            ss_res = np.sum((arr - y_pred) ** 2)
            ss_tot = np.sum((arr - np.mean(arr)) ** 2)
            self.ack_time_trend_r2 = float(1 - (ss_res / ss_tot)) if ss_tot > 0 else 0.0

    def compute_resolve_stats(self) -> None:
        if not self.resolve_times_minutes:
            return
        import numpy as np

        arr = np.array(self.resolve_times_minutes)
        self.median_resolve_time = float(np.median(arr))
        self.mean_resolve_time = float(np.mean(arr))
        self.p90_resolve_time = float(np.percentile(arr, 90))

    def compute_burnout_risk(
        self,
        ack_time_weight: float = 0.4,
        trend_weight: float = 0.3,
        consecutive_days_weight: float = 0.3,
        ack_time_threshold_minutes: float = 30.0,
        trend_threshold_per_alert: float = 2.0,
        consecutive_days_threshold: int = 5,
    ) -> None:
        ack_score = 0.0
        if self.median_ack_time > 0:
            ack_score = min(self.median_ack_time / ack_time_threshold_minutes, 2.0)

        trend_score = 0.0
        if self.ack_time_trend_slope > 0:
            trend_score = min(self.ack_time_trend_slope / trend_threshold_per_alert, 2.0)

        consecutive_score = 0.0
        if self.max_consecutive_oncall_days > 0:
            consecutive_score = min(
                self.max_consecutive_oncall_days / consecutive_days_threshold, 2.0
            )

        self.burnout_risk_score = (
            ack_time_weight * ack_score
            + trend_weight * trend_score
            + consecutive_days_weight * consecutive_score
        )

        if self.burnout_risk_score >= 1.5:
            self.risk_level = "critical"
        elif self.burnout_risk_score >= 1.0:
            self.risk_level = "high"
        elif self.burnout_risk_score >= 0.5:
            self.risk_level = "medium"
        else:
            self.risk_level = "low"


class BurnoutReport(BaseModel):
    generated_at: datetime
    window_start: datetime
    window_end: datetime
    engineers: list[EngineerMetrics]
    total_alerts: int
    total_engineers: int
    high_risk_count: int = 0
    critical_risk_count: int = 0

    def compute_summary(self) -> None:
        self.total_engineers = len(self.engineers)
        self.total_alerts = sum(e.total_alerts for e in self.engineers)
        self.high_risk_count = sum(
            1 for e in self.engineers if e.risk_level == "high"
        )
        self.critical_risk_count = sum(
            1 for e in self.engineers if e.risk_level == "critical"
        )


def parse_pagerduty_alerts(data: dict) -> list[RawAlert]:
    alerts = []
    for item in data.get("alerts", []):
        try:
            alert = RawAlert(
                id=str(item.get("id", "")),
                source=AlertSource.PAGERDUTY,
                title=item.get("summary", item.get("title", "Unknown")),
                severity=AlertSeverity(
                    item.get("severity", "info").lower()
                ),
                status=AlertStatus(
                    item.get("status", "triggered").lower()
                ),
                created_at=item.get("created_at", item.get("created_on", "")),
                acknowledged_at=item.get("acknowledged_at"),
                resolved_at=item.get("resolved_at"),
                assignee_id=str(
                    item.get("assignee", {}).get("id", item.get("user", {}).get("id", ""))
                ),
                assignee_name=item.get("assignee", {}).get(
                    "name", item.get("user", {}).get("name", "Unknown")
                ),
                assignee_email=item.get("assignee", {}).get("email"),
                service_name=item.get("service", {}).get("name"),
                service_id=str(item.get("service", {}).get("id", "")),
            )
            alerts.append(alert)
        except Exception:
            continue
    return alerts


def parse_opsgenie_alerts(data: dict) -> list[RawAlert]:
    alerts = []
    status_map = {"open": "triggered", "acknowledged": "acknowledged", "closed": "resolved"}
    priority_map = {"p1": "critical", "p2": "high", "p3": "medium", "p4": "low", "p5": "info"}
    for item in data.get("data", []):
        try:
            raw_status = item.get("status", "open").lower()
            mapped_status = status_map.get(raw_status, "triggered")

            raw_priority = item.get("priority", "P3").lower()
            mapped_severity = priority_map.get(raw_priority, "medium")

            alert = RawAlert(
                id=str(item.get("alertId", item.get("id", ""))),
                source=AlertSource.OPSGENIE,
                title=item.get("message", item.get("description", "Unknown")),
                severity=AlertSeverity(mapped_severity),
                status=AlertStatus(mapped_status),
                created_at=item.get("createdAt", item.get("created_at", "")),
                acknowledged_at=item.get("acknowledgedAt"),
                resolved_at=item.get("closedAt", item.get("resolvedAt")),
                assignee_id=str(item.get("owner", {}).get("id", item.get("owner", "unknown"))),
                assignee_name=item.get("owner", {}).get("name", item.get("owner", "Unknown")),
                assignee_email=item.get("owner", {}).get("email"),
                service_name=item.get("tags", [None])[0] if item.get("tags") else None,
                service_id=None,
            )
            alerts.append(alert)
        except Exception:
            continue
    return alerts


def parse_pagerduty_schedules(data: dict) -> list[OnCallShift]:
    shifts = []
    for schedule in data.get("schedules", []):
        for entry in schedule.get("final_schedule", {}).get("rendered_schedule_entries", []):
            try:
                user = entry.get("user", {})
                shift = OnCallShift(
                    user_id=str(user.get("id", "")),
                    user_name=user.get("name", "Unknown"),
                    user_email=user.get("email"),
                    start=entry.get("start", ""),
                    end=entry.get("end", ""),
                    schedule_name=schedule.get("name"),
                    schedule_id=str(schedule.get("id", "")),
                )
                shifts.append(shift)
            except Exception:
                continue
    return shifts


def parse_opsgenie_schedules(data: dict) -> list[OnCallShift]:
    shifts = []
    for schedule in data.get("data", []):
        for participant in schedule.get("participants", []):
            try:
                shift = OnCallShift(
                    user_id=str(participant.get("id", participant.get("username", ""))),
                    user_name=participant.get("name", participant.get("username", "Unknown")),
                    user_email=participant.get("email"),
                    start=schedule.get("startDate", ""),
                    end=schedule.get("endDate", ""),
                    schedule_name=schedule.get("name"),
                    schedule_id=str(schedule.get("id", "")),
                )
                shifts.append(shift)
            except Exception:
                continue
    return shifts