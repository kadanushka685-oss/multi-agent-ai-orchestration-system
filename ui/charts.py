"""Plotly figures for the dashboard. Each function takes plain data and returns a Figure."""
from __future__ import annotations

import plotly.graph_objects as go

ACCENT = "#1F4E79"
STATUS_COLORS = {
    "completed": "#2F855A",
    "completed_with_warnings": "#B7791F",
    "failed": "#B42318",
    "running": "#2B6CB0",
    "awaiting_approval": "#6B46C1",
    "planning": "#718096",
}
_LAYOUT = dict(template="simple_white", margin=dict(l=20, r=20, t=50, b=20), height=330)


def status_chart(counts: dict) -> go.Figure:
    if counts:
        labels = list(counts)
        values = list(counts.values())
        colors = [STATUS_COLORS.get(label, "#A0AEC0") for label in labels]
    else:
        labels, values, colors = ["no tasks yet"], [1], ["#E2E8F0"]
    figure = go.Figure(go.Pie(labels=labels, values=values, hole=0.5, marker=dict(colors=colors)))
    figure.update_layout(title="Tasks by status", **_LAYOUT)
    return figure


def agent_success_chart(rows: list[dict]) -> go.Figure:
    names = [row["agent"] for row in rows]
    rates = [round((row["rate"] or 0.0) * 100, 1) for row in rows]
    figure = go.Figure(go.Bar(x=names, y=rates, marker_color=ACCENT, text=[f"{v:.0f}%" for v in rates]))
    figure.update_layout(title="Agent success rate (completed / attempted steps)", yaxis=dict(range=[0, 100], title="%"), **_LAYOUT)
    return figure


def daily_chart(rows: list[tuple]) -> go.Figure:
    figure = go.Figure(go.Bar(x=[day for day, _ in rows], y=[count for _, count in rows], marker_color=ACCENT))
    figure.update_layout(title="Tasks created per day", yaxis=dict(title="Tasks"), **_LAYOUT)
    return figure


def duration_chart(steps: list[dict]) -> go.Figure:
    figure = go.Figure(go.Bar(
        x=[f"{s['step_id']} {s['agent']}" for s in steps],
        y=[s["duration_seconds"] for s in steps],
        marker_color=ACCENT,
    ))
    figure.update_layout(title="Duration per step (seconds)", **_LAYOUT)
    return figure
