from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .models import (
    AlertStatus,
    BurnoutReport,
    EngineerMetrics,
    OnCallShift,
    RawAlert,
    parse_opsgenie_alerts,
    parse_opsgenie_schedules,
    parse_pagerduty_alerts,
    parse_pagerduty_schedules,
)


class BurnoutEngine:
    def __init__(
        self,
        window_days: int = 30,
        ack_time_weight: float = 0.4,
        trend_weight: float = 0.3,
        consecutive_days_weight: float = 0.3,
        ack_time_threshold_minutes: float = 30.0,
        trend_threshold_per_alert: float = 2.0,
        consecutive_days_threshold: int = 5,
    ):
        self.window_days = window_days
        self.ack_time_weight = ack_time_weight
        self.trend_weight = trend_weight
        self.consecutive_days_weight = consecutive_days_weight
        self.ack_time_threshold_minutes = ack_time_threshold_minutes
        self.trend_threshold_per_alert = trend_threshold_per_alert
        self.consecutive_days_threshold = consecutive_days_threshold

    def process_alerts(
        self,
        alerts: list[RawAlert],
        shifts: list[OnCallShift],
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> BurnoutReport:
        if window_end is None:
            window_end = datetime.now()
        if window_start is None:
            window_start = window_end - timedelta(days=self.window_days)

        filtered_alerts = [
            a for a in alerts
            if window_start <= a.created_at <= window_end
        ]

        filtered_shifts = [
            s for s in shifts
            if not (s.end < window_start or s.start > window_end)
        ]

        engineer_data: dict[str, EngineerMetrics] = {}

        for alert in filtered_alerts:
            uid = alert.assignee_id
            if uid not in engineer_data:
                engineer_data[uid] = EngineerMetrics(
                    user_id=uid,
                    user_name=alert.assignee_name,
                    user_email=alert.assignee_email,
                )

            eng = engineer_data[uid]
            eng.total_alerts += 1

            if alert.status in (AlertStatus.ACKNOWLEDGED, AlertStatus.RESOLVED) and alert.acknowledged_at:
                eng.acked_alerts += 1
                ack_time = (alert.acknowledged_at - alert.created_at).total_seconds() / 60
                if ack_time >= 0:
                    eng.ack_times_minutes.append(ack_time)

            if alert.status == AlertStatus.RESOLVED and alert.resolved_at:
                eng.resolved_alerts += 1
                resolve_time = (alert.resolved_at - alert.created_at).total_seconds() / 60
                if resolve_time >= 0:
                    eng.resolve_times_minutes.append(resolve_time)

        for shift in filtered_shifts:
            uid = shift.user_id
            if uid not in engineer_data:
                engineer_data[uid] = EngineerMetrics(
                    user_id=uid,
                    user_name=shift.user_name,
                    user_email=shift.user_email,
                )

        self._compute_consecutive_days(engineer_data, filtered_shifts, window_start, window_end)

        for eng in engineer_data.values():
            eng.compute_ack_stats()
            eng.compute_resolve_stats()
            eng.compute_burnout_risk(
                ack_time_weight=self.ack_time_weight,
                trend_weight=self.trend_weight,
                consecutive_days_weight=self.consecutive_days_weight,
                ack_time_threshold_minutes=self.ack_time_threshold_minutes,
                trend_threshold_per_alert=self.trend_threshold_per_alert,
                consecutive_days_threshold=self.consecutive_days_threshold,
            )

        engineers_list = list(engineer_data.values())
        engineers_list.sort(key=lambda e: e.burnout_risk_score, reverse=True)

        report = BurnoutReport(
            generated_at=datetime.now(),
            window_start=window_start,
            window_end=window_end,
            engineers=engineers_list,
            total_alerts=0,
            total_engineers=0,
        )
        report.compute_summary()
        return report

    def _compute_consecutive_days(
        self,
        engineer_data: dict[str, EngineerMetrics],
        shifts: list[OnCallShift],
        window_start: datetime,
        window_end: datetime,
    ) -> None:
        shifts_by_user: dict[str, list[OnCallShift]] = defaultdict(list)
        for shift in shifts:
            shifts_by_user[shift.user_id].append(shift)

        for uid, user_shifts in shifts_by_user.items():
            user_shifts.sort(key=lambda s: s.start)

            oncall_dates: set[datetime] = set()
            for shift in user_shifts:
                start = max(shift.start, window_start)
                end = min(shift.end, window_end)
                current = start.replace(hour=0, minute=0, second=0, microsecond=0)
                end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)
                while current <= end_day:
                    oncall_dates.add(current)
                    current += timedelta(days=1)

            if uid in engineer_data:
                eng = engineer_data[uid]
                eng.total_oncall_days_in_window = len(oncall_dates)

                if oncall_dates:
                    sorted_dates = sorted(oncall_dates)
                    max_streak = 1
                    current_streak = 1
                    for i in range(1, len(sorted_dates)):
                        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
                            current_streak += 1
                            max_streak = max(max_streak, current_streak)
                        else:
                            current_streak = 1
                    eng.max_consecutive_oncall_days = max_streak
                    eng.consecutive_oncall_days = current_streak


def load_alerts_from_file(filepath: str, source: str) -> list[RawAlert]:
    import json

    with open(filepath) as f:
        data = json.load(f)

    if source == "pagerduty":
        return parse_pagerduty_alerts(data)
    elif source == "opsgenie":
        return parse_opsgenie_alerts(data)
    else:
        raise ValueError(f"Unknown source: {source}")


def load_shifts_from_file(filepath: str, source: str) -> list[OnCallShift]:
    import json

    with open(filepath) as f:
        data = json.load(f)

    if source == "pagerduty":
        return parse_pagerduty_schedules(data)
    elif source == "opsgenie":
        return parse_opsgenie_schedules(data)
    else:
        raise ValueError(f"Unknown source: {source}")