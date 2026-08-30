from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from .engine import BurnoutEngine, load_alerts_from_file, load_shifts_from_file
from .models import AlertSource, BurnoutReport

app = typer.Typer(
    name="oncall-burnout",
    help="On-Call Burnout Early-Warning Dashboard",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()


def risk_color(level: str) -> str:
    colors = {
        "critical": "red",
        "high": "orange3",
        "medium": "yellow",
        "low": "green",
    }
    return colors.get(level, "white")


def risk_emoji(level: str) -> str:
    emojis = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢",
    }
    return emojis.get(level, "⚪")


@app.command()
def analyze(
    alerts_file: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Path to alerts JSON export (PagerDuty or OpsGenie format)",
    ),
    shifts_file: Path | None = typer.Argument(
        None,
        exists=True,
        readable=True,
        help="Path to on-call schedule JSON export (optional)",
    ),
    source: AlertSource = typer.Option(
        AlertSource.PAGERDUTY,
        "--source",
        "-s",
        help="Source of the export data",
        case_sensitive=False,
    ),
    window_days: int = typer.Option(
        30,
        "--window",
        "-w",
        help="Analysis window in days",
        min=1,
        max=365,
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write JSON report to file",
    ),
    ack_weight: float = typer.Option(
        0.4,
        "--ack-weight",
        help="Weight for median ack time in risk score",
        min=0.0,
        max=1.0,
    ),
    trend_weight: float = typer.Option(
        0.3,
        "--trend-weight",
        help="Weight for ack time trend in risk score",
        min=0.0,
        max=1.0,
    ),
    consecutive_weight: float = typer.Option(
        0.3,
        "--consecutive-weight",
        help="Weight for consecutive on-call days in risk score",
        min=0.0,
        max=1.0,
    ),
    ack_threshold: float = typer.Option(
        30.0,
        "--ack-threshold",
        help="Ack time threshold in minutes for max risk",
        min=1.0,
    ),
    trend_threshold: float = typer.Option(
        2.0,
        "--trend-threshold",
        help="Trend slope threshold (min/alert) for max risk",
        min=0.1,
    ),
    consecutive_threshold: int = typer.Option(
        5,
        "--consecutive-threshold",
        help="Consecutive days threshold for max risk",
        min=1,
    ),
    top: int = typer.Option(
        10,
        "--top",
        "-t",
        help="Number of top at-risk engineers to show",
        min=1,
    ),
    no_dashboard: bool = typer.Option(
        False,
        "--no-dashboard",
        help="Skip dashboard output, only print summary",
    ),
):
    """Analyze on-call alerts and schedules to compute burnout risk scores."""
    total_weight = ack_weight + trend_weight + consecutive_weight
    if abs(total_weight - 1.0) > 0.001:
        console.print(
            f"[red]Error:[/red] Weights must sum to 1.0 (got {total_weight:.3f})"
        )
        raise typer.Exit(1)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Loading alerts...", total=None)
        try:
            alerts = load_alerts_from_file(str(alerts_file), source.value)
        except Exception as e:
            console.print(f"[red]Error loading alerts:[/red] {e}")
            raise typer.Exit(1)
        progress.update(task, description=f"Loaded {len(alerts)} alerts")

        shifts = []
        if shifts_file:
            progress.update(task, description="Loading schedules...")
            try:
                shifts = load_shifts_from_file(str(shifts_file), source.value)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not load schedules:[/yellow] {e}")
            progress.update(task, description=f"Loaded {len(shifts)} shifts")
        else:
            console.print(
                "[yellow]No schedule file provided — consecutive-day analysis will be limited[/yellow]"
            )

        progress.update(task, description="Computing burnout risk...")
        engine = BurnoutEngine(
            window_days=window_days,
            ack_time_weight=ack_weight,
            trend_weight=trend_weight,
            consecutive_days_weight=consecutive_weight,
            ack_time_threshold_minutes=ack_threshold,
            trend_threshold_per_alert=trend_threshold,
            consecutive_days_threshold=consecutive_threshold,
        )
        report = engine.process_alerts(alerts, shifts)

    if output:
        try:
            with open(output, "w") as f:
                json.dump(report.model_dump(mode="json"), f, indent=2, default=str)
            console.print(f"[green]Report written to[/green] {output}")
        except Exception as e:
            console.print(f"[red]Error writing output:[/red] {e}")

    if not no_dashboard:
        render_dashboard(report, top)
    else:
        render_summary(report)


def render_dashboard(report: BurnoutReport, top_n: int) -> None:
    console.print()
    console.print(
        Panel(
            Align.center(
                Text("🔥 On-Call Burnout Early-Warning Dashboard", style="bold white")
            ),
            subtitle=f"Window: {report.window_start.date()} → {report.window_end.date()} | Generated: {report.generated_at.strftime('%Y-%m-%d %H:%M')}",
            style="blue",
        )
    )
    console.print()

    summary_table = Table(show_header=False, box=None, padding=(0, 2))
    summary_table.add_column("Metric", style="bold cyan")
    summary_table.add_column("Value", style="white")

    summary_table.add_row("Total Engineers", str(report.total_engineers))
    summary_table.add_row("Total Alerts", str(report.total_alerts))
    summary_table.add_row("🔴 Critical Risk", str(report.critical_risk_count))
    summary_table.add_row("🟠 High Risk", str(report.high_risk_count))
    summary_table.add_row(
        "🟡 Medium Risk",
        str(sum(1 for e in report.engineers if e.risk_level == "medium")),
    )
    summary_table.add_row(
        "🟢 Low Risk",
        str(sum(1 for e in report.engineers if e.risk_level == "low")),
    )

    console.print(Panel(summary_table, title="[bold]Summary[/bold]", border_style="blue"))
    console.print()

    table = Table(
        title=f"Top {min(top_n, len(report.engineers))} Engineers by Burnout Risk",
        show_header=True,
        header_style="bold magenta",
        border_style="blue",
    )
    table.add_column("Rank", justify="right", style="dim", width=5)
    table.add_column("Engineer", style="bold", width=22)
    table.add_column("Risk", justify="center", width=12)
    table.add_column("Score", justify="right", width=8)
    table.add_column("Alerts", justify="right", width=7)
    table.add_column("Acked", justify="right", width=7)
    table.add_column("Median Ack", justify="right", width=10)
    table.add_column("Trend", justify="right", width=10)
    table.add_column("Max Consecutive", justify="right", width=14)
    table.add_column("On-Call Days", justify="right", width=12)

    for i, eng in enumerate(report.engineers[:top_n], 1):
        risk_style = risk_color(eng.risk_level)
        trend_str = f"{eng.ack_time_trend_slope:+.2f}/alert" if eng.ack_time_trend_slope != 0 else "—"
        trend_style = "red" if eng.ack_time_trend_slope > 0 else "green" if eng.ack_time_trend_slope < 0 else "dim"

        table.add_row(
            str(i),
            eng.user_name[:20],
            f"[{risk_style}]{risk_emoji(eng.risk_level)} {eng.risk_level.upper()}[/{risk_style}]",
            f"[{risk_style}]{eng.burnout_risk_score:.2f}[/{risk_style}]",
            str(eng.total_alerts),
            str(eng.acked_alerts),
            f"{eng.median_ack_time:.1f}m" if eng.median_ack_time > 0 else "—",
            f"[{trend_style}]{trend_str}[/{trend_style}]",
            str(eng.max_consecutive_oncall_days) if eng.max_consecutive_oncall_days > 0 else "—",
            str(eng.total_oncall_days_in_window) if eng.total_oncall_days_in_window > 0 else "—",
        )

    console.print(table)
    console.print()

    if report.critical_risk_count > 0 or report.high_risk_count > 0:
        console.print(
            Panel(
                Text.from_markup(
                    f"[bold red]⚠ ACTION REQUIRED:[/bold red] "
                    f"{report.critical_risk_count} critical + {report.high_risk_count} high-risk engineers detected.\n"
                    "Consider: redistributing on-call load, adding coverage, or scheduling recovery time."
                ),
                border_style="red",
            )
        )
    else:
        console.print(
            Panel(
                Text.from_markup(
                    "[bold green]✓ All engineers within acceptable risk thresholds[/bold green]"
                ),
                border_style="green",
            )
        )


def render_summary(report: BurnoutReport) -> None:
    console.print(f"Analyzed {report.total_engineers} engineers, {report.total_alerts} alerts")
    console.print(f"Critical: {report.critical_risk_count} | High: {report.high_risk_count}")
    for eng in report.engineers:
        if eng.risk_level in ("critical", "high"):
            console.print(
                f"  {risk_emoji(eng.risk_level)} {eng.user_name}: "
                f"score={eng.burnout_risk_score:.2f}, "
                f"ack={eng.median_ack_time:.1f}m, "
                f"consecutive={eng.max_consecutive_oncall_days}d"
            )


@app.command()
def generate_sample(
    output_dir: Path = typer.Option(
        Path("data/sample"),
        "--output-dir",
        "-o",
        help="Directory to write sample files",
    ),
    engineers: int = typer.Option(
        8,
        "--engineers",
        "-e",
        help="Number of engineers in sample",
        min=2,
        max=50,
    ),
    days: int = typer.Option(
        30,
        "--days",
        "-d",
        help="Days of history to generate",
        min=7,
        max=90,
    ),
    seed: int = typer.Option(
        42,
        "--seed",
        help="Random seed for reproducibility",
    ),
):
    """Generate sample PagerDuty-format alert and schedule data for testing."""
    import random
    from datetime import timezone

    random.seed(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    engineer_names = [
        "Alice Chen", "Bob Martinez", "Carol Singh", "David Kim",
        "Eva Rodriguez", "Frank Thompson", "Grace Lee", "Henry Patel",
        "Iris Wu", "Jack O'Brien", "Karen Zhang", "Luis Garcia",
    ][:engineers]

    engineers_data = [
        {
            "id": f"P{1000 + i}",
            "name": name,
            "email": f"{name.lower().replace(' ', '.')}@company.com",
        }
        for i, name in enumerate(engineer_names)
    ]

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_start = now - timedelta(days=days)

    alerts = []
    alert_id = 1

    for eng in engineers_data:
        base_ack_minutes = random.uniform(5, 45)
        trend_factor = random.uniform(-0.5, 3.0)

        num_alerts = random.randint(5, 30)
        for j in range(num_alerts):
            created = window_start + timedelta(
                hours=random.uniform(0, days * 24)
            )

            ack_delay = max(0.5, base_ack_minutes + trend_factor * j + random.uniform(-5, 10))
            acknowledged = created + timedelta(minutes=ack_delay)
            resolved = acknowledged + timedelta(minutes=random.uniform(10, 120))

            severities = ["critical", "high", "medium", "low", "info"]
            statuses = ["triggered", "acknowledged", "resolved"]

            alerts.append({
                "id": str(alert_id),
                "summary": f"Alert {alert_id} for {eng['name']}",
                "severity": random.choice(severities),
                "status": random.choices(statuses, weights=[0.1, 0.3, 0.6])[0],
                "created_at": created.isoformat() + "Z",
                "acknowledged_at": acknowledged.isoformat() + "Z",
                "resolved_at": resolved.isoformat() + "Z",
                "assignee": {
                    "id": eng["id"],
                    "name": eng["name"],
                    "email": eng["email"],
                },
                "service": {
                    "id": f"S{random.randint(1, 5)}",
                    "name": f"service-{random.randint(1, 5)}",
                },
            })
            alert_id += 1

    alerts_data = {"alerts": alerts}
    alerts_file = output_dir / "alerts.json"
    with open(alerts_file, "w") as f:
        json.dump(alerts_data, f, indent=2)

    schedules = []
    for i, eng in enumerate(engineers_data):
        num_shifts = random.randint(3, 10)
        shift_start = window_start + timedelta(days=random.uniform(0, 7))

        for _ in range(num_shifts):
            shift_length = random.randint(1, 7)
            shift_end = shift_start + timedelta(days=shift_length)

            schedules.append({
                "id": f"SCH{100 + i * 10 + _}",
                "name": f"Primary Rotation {i + 1}",
                "final_schedule": {
                    "rendered_schedule_entries": [{
                        "start": shift_start.isoformat() + "Z",
                        "end": shift_end.isoformat() + "Z",
                        "user": {
                            "id": eng["id"],
                            "name": eng["name"],
                            "email": eng["email"],
                        },
                    }]
                },
            })
            shift_start = shift_end + timedelta(days=random.randint(1, 10))

    schedules_data = {"schedules": schedules}
    shifts_file = output_dir / "schedules.json"
    with open(shifts_file, "w") as f:
        json.dump(schedules_data, f, indent=2)

    console.print("[green]Generated sample data:[/green]")
    console.print(f"  Alerts: {alerts_file} ({len(alerts)} alerts)")
    console.print(f"  Schedules: {shifts_file} ({len(schedules)} shifts)")
    console.print(f"  Engineers: {engineers} over {days} days")


if __name__ == "__main__":
    app()