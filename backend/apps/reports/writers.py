"""CSV, Excel and PDF rendering for reports (spec A13)."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any

from django.utils import timezone

from apps.reports.registry import Column, Report


def fmt(col: Column, v: Any) -> Any:
    """Display value; numbers stay numbers (for Excel), everything else becomes text."""
    if v is None or v == "":
        return ""
    if col.kind == "datetime" and isinstance(v, datetime):
        return timezone.localtime(v).strftime("%Y-%m-%d %H:%M")
    if col.kind == "date" and isinstance(v, date):
        return v.isoformat()
    if col.kind == "duration":
        s = int(v)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    if col.kind == "km":
        return round(float(v), 2)
    if col.kind in ("kmh", "percent"):
        return round(float(v), 1)
    if col.kind == "number":
        return v
    return str(v)


def to_csv(report: Report, rows: list[dict[str, Any]]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([c.label for c in report.columns])
    for r in rows:
        w.writerow([fmt(c, r.get(c.key)) for c in report.columns])
    return ("\ufeff" + out.getvalue()).encode("utf-8")          # BOM: Excel opens UTF-8 (Swahili names) correctly


def to_xlsx(report: Report, rows: list[dict[str, Any]], heading: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(report.title[:31])
    title = WriteOnlyCell(ws, value=report.title)
    title.font = Font(bold=True, size=13)
    ws.append([title])
    ws.append([heading])
    ws.append([])
    header = []
    for c in report.columns:
        cell = WriteOnlyCell(ws, value=c.label)
        cell.font = Font(bold=True)
        header.append(cell)
    ws.append(header)
    for r in rows:
        ws.append([fmt(c, r.get(c.key)) for c in report.columns])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(report: Report, rows: list[dict[str, Any]], heading: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=report.title)
    styles = getSampleStyleSheet()
    body = [[c.label for c in report.columns]] + [[str(fmt(c, r.get(c.key))) for c in report.columns] for r in rows]
    table = Table(body, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7.5), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14323b")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f4f3")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d1cf")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story = [Paragraph(report.title, styles["Title"]), Paragraph(heading, styles["Normal"]), Spacer(1, 4 * mm)]
    story.append(table if rows else Paragraph("No data in this period.", styles["Normal"]))
    doc.build(story)
    return buf.getvalue()


CONTENT_TYPES = {"csv": "text/csv; charset=utf-8",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "pdf": "application/pdf"}


def render(report: Report, rows: list[dict[str, Any]], fmt_: str, heading: str) -> bytes:
    if fmt_ == "csv":
        return to_csv(report, rows)
    if fmt_ == "xlsx":
        return to_xlsx(report, rows, heading)
    return to_pdf(report, rows, heading)
