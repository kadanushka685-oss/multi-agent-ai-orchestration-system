"""PDF export built with ReportLab's platypus layout engine."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _esc(value) -> str:
    return escape("" if value is None else str(value))


def _fmt_seconds(value) -> str:
    return "n/a" if value is None else f"{float(value):.2f}"


def _markdown_flowables(text: str, styles: dict) -> list:
    """Render the report's simple markdown: headings, quotes, bullets and paragraphs."""
    flowables = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flowables.append(Spacer(1, 4))
        elif line.startswith("# "):
            flowables.append(Paragraph(_esc(line[2:]), styles["heading2"]))
        elif line.startswith("## "):
            flowables.append(Paragraph(_esc(line[3:]), styles["heading3"]))
        elif line.startswith(">"):
            flowables.append(Paragraph(f"<i>{_esc(line[1:].strip())}</i>", styles["body"]))
        elif line.startswith("- "):
            flowables.append(Paragraph("&bull; " + _esc(line[2:]), styles["bullet"]))
        else:
            flowables.append(Paragraph(_esc(line), styles["body"]))
    return flowables


def build_pdf(report: dict) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=report["title"],
        author="Multi-Agent Orchestration System",
    )
    base = getSampleStyleSheet()
    styles = {
        "title": base["Title"],
        "heading2": base["Heading2"],
        "heading3": base["Heading3"],
        "body": base["BodyText"],
        "bullet": ParagraphStyle("bullet", parent=base["BodyText"], leftIndent=12),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontSize=8, leading=10),
    }
    grid = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF4")),
    ])

    story = [Paragraph(_esc(report["title"]), styles["title"]), Spacer(1, 6)]

    meta = [
        ["Task ID", report["task_id"]],
        ["Status", report["status"]],
        ["Mode", report["mode"]],
        ["Duration (seconds)", _fmt_seconds(report["duration_seconds"])],
        ["Retries", str(report["total_retries"])],
        ["Revisions", str(report["revisions"])],
        ["Agents selected", ", ".join(report["selected_agents"])],
        ["Generated", report["generated_at"]],
    ]
    meta_table = Table(
        [[Paragraph(_esc(k), styles["small"]), Paragraph(_esc(v), styles["small"])] for k, v in meta],
        colWidths=[4 * cm, 12 * cm],
    )
    meta_table.setStyle(grid)
    story += [meta_table, Spacer(1, 10)]

    story.append(Paragraph("Original task", styles["heading2"]))
    story.append(Paragraph(_esc(report["original_task"]), styles["body"]))

    story.append(Paragraph("Execution plan", styles["heading2"]))
    header = ["Step", "Agent", "Title", "Depends on", "Status", "Attempts"]
    rows = [[Paragraph(_esc(h), styles["small"]) for h in header]]
    for step in report["plan"]:
        values = [
            step["step_id"], step["agent"], step["title"],
            ", ".join(step["depends_on"]) or "-", step["status"], str(step["attempts"]),
        ]
        rows.append([Paragraph(_esc(v), styles["small"]) for v in values])
    plan_table = Table(rows, colWidths=[1.6 * cm, 2.6 * cm, 6.8 * cm, 2.6 * cm, 2.4 * cm, 1.8 * cm], repeatRows=1)
    plan_table.setStyle(grid)
    story += [plan_table, Spacer(1, 8)]

    story.append(Paragraph("Agents involved", styles["heading2"]))
    for agent in report["agents_involved"]:
        story.append(Paragraph(_esc(
            f"{agent['agent']}: {agent['steps']} step(s), {agent['completed']} completed, "
            f"{agent['failed']} failed, {agent['skipped']} skipped."
        ), styles["body"]))

    story.append(Paragraph("Agent outputs", styles["heading2"]))
    for output in report["agent_outputs"]:
        story.append(Paragraph(f"<b>{_esc(output['step_id'])} ({_esc(output['agent'])})</b>: {_esc(output['summary'])}", styles["body"]))
        for warning in output["warnings"]:
            story.append(Paragraph("Warning: " + _esc(warning), styles["bullet"]))

    verification = report["verification"]
    story.append(Paragraph("Verification results", styles["heading2"]))
    story.append(Paragraph(_esc(
        f"Verdict: {verification['verdict']}. Verified: {verification['verified']}. "
        f"Unverified: {verification['unverified']}. Labelled inferences: {verification['inferences']}. "
        f"Unsupported: {verification['unsupported']}."
    ), styles["body"]))
    for issue in verification["issues"]:
        story.append(Paragraph(f"&bull; {_esc(issue['severity'])} ({_esc(issue['kind'])}): {_esc(issue['message'])}", styles["bullet"]))

    story.append(Paragraph("Final response", styles["heading2"]))
    story.extend(_markdown_flowables(report["final_response"] or "No final response was produced.", styles))

    doc.build(story)
    return buffer.getvalue()
