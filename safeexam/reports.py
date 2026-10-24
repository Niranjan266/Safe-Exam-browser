"""
Evidence reporting.

The summary report (admin.report_pdf) answers "how did the cohort do?".
This module answers a different and more consequential question: "what exactly
did THIS candidate do, and what is the proof?" — the document you attach to a
malpractice finding, hand to an examination committee, or give to the candidate
when they contest a disqualification.

That framing drives three choices:

  * Everything is timestamped in UTC and also as an offset from the start of the
    attempt, because "14:32:07" means nothing to a reader six weeks later but
    "18m 04s into a 60 minute exam" does.
  * Webcam stills are embedded, not linked. A report that depends on the server
    still being up, and on the reader having an admin login, is not evidence.
  * Nothing is inferred or editorialised. The report states what was recorded
    and the rule that was applied; it does not conclude that cheating occurred.
"""
from __future__ import annotations

import io
from datetime import datetime

from . import framestore, proctoring
from .services import fmt_marks
from .timeutil import utcnow

# Layout constants for the snapshot contact sheet.
SNAPS_PER_ROW = 3
SNAP_WIDTH_CM = 5.2
# Cap on embedded stills. A three hour exam at one snapshot per 20s is 540
# images; nobody reads that, and the PDF would be enormous. We keep a spread
# across the whole attempt plus every flagged frame (see _select_snapshots).
MAX_SNAPSHOTS = 24


def _fmt_dt(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else "—"


def _fmt_offset(value, start):
    """Elapsed time into the attempt, which is what a reviewer actually needs."""
    if not value or not start:
        return "—"
    secs = int((value - start).total_seconds())
    if secs < 0:
        return "—"
    return f"{secs // 60}m {secs % 60:02d}s"


def _select_snapshots(snaps):
    """
    Keep every flagged snapshot, then fill the remaining budget with an evenly
    spaced sample so the reader still sees the whole attempt rather than just
    its first few minutes.
    """
    if len(snaps) <= MAX_SNAPSHOTS:
        return snaps

    flagged = [s for s in snaps if s.flagged]
    rest = [s for s in snaps if not s.flagged]
    budget = max(0, MAX_SNAPSHOTS - len(flagged))

    if budget and rest:
        step = len(rest) / float(budget)
        sampled = [rest[int(i * step)] for i in range(budget)]
    else:
        sampled = []

    chosen = flagged + sampled
    chosen.sort(key=lambda s: s.captured_at or datetime.min)
    return chosen[:MAX_SNAPSHOTS]


def _snapshot_flowable(snap, width, Image):
    """
    Turn a stored JPEG into a reportlab Image, or None if it cannot be read.

    A snapshot whose bytes are missing or corrupt must not take the whole
    report down — a partially illustrated report is still usable evidence,
    a 500 is not.
    """
    data = framestore.load_snapshot(snap)
    if not data:
        return None
    try:
        from reportlab.lib.utils import ImageReader

        reader = ImageReader(io.BytesIO(data))
        iw, ih = reader.getSize()
        height = width * (ih / float(iw)) if iw else width * 0.75
        return Image(io.BytesIO(data), width=width, height=height)
    except Exception:
        # Unknown aspect ratio: fall back to 4:3 rather than dropping the frame.
        try:
            return Image(io.BytesIO(data), width=width, height=width * 0.75)
        except Exception:
            return None


def build_evidence_pdf(submission, student, exam, pass_mark=None):
    """
    Render a single attempt's evidence report and return a BytesIO positioned
    at the start, ready to hand to send_file.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)
    caption = ParagraphStyle("caption", parent=small, alignment=TA_CENTER,
                             textColor=colors.HexColor("#475569"))
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8.5, leading=10.5)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
        title=f"SafeExam evidence report — attempt {submission.id}",
        author="SafeExam",
    )

    def header_style(t, header_bg="#1e293b"):
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    def facts_table(rows, label_bg="#334155"):
        t = Table(rows, colWidths=[4.6 * cm, 12.0 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor(label_bg)),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    story = []

    # ---------------------------------------------------------------- header
    story.append(Paragraph("Examination Integrity — Candidate Evidence Report", styles["Title"]))
    story.append(Paragraph(
        f"{exam.title if exam else 'Unknown exam'} &nbsp;·&nbsp; attempt #{submission.id}",
        ParagraphStyle("sub", parent=styles["Normal"], alignment=TA_CENTER,
                       fontSize=10, textColor=colors.HexColor("#475569")),
    ))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        utcnow().strftime("Generated %Y-%m-%d %H:%M UTC — all times in this report are UTC."),
        caption,
    ))
    story.append(Spacer(1, 14))

    # ------------------------------------------------------------- candidate
    name = (getattr(student, "full_name", None) or getattr(student, "username", "—")) if student else "—"
    story.append(Paragraph("Candidate", styles["Heading2"]))
    story.append(facts_table([
        ["Name", name],
        ["Username", getattr(student, "username", "—") if student else "—"],
        ["Email", getattr(student, "email", None) or "—"],
        ["Exam", exam.title if exam else "—"],
    ]))
    story.append(Spacer(1, 14))

    # --------------------------------------------------------------- attempt
    duration = "—"
    if submission.started_at and submission.completed_at:
        secs = int((submission.completed_at - submission.started_at).total_seconds())
        duration = f"{secs // 60}m {secs % 60:02d}s"

    if submission.total:
        pct = round((submission.score or 0) / submission.total * 100)
        score_txt = f"{fmt_marks(submission.score)} / {fmt_marks(submission.total)}  ({pct}%)"
        if submission.disqualified:
            # Never print "PASS" on a disqualified attempt. The raw marks are
            # still shown because a review may reinstate them, but presenting a
            # pass verdict on an attempt that was terminated for misconduct
            # would misrepresent the record in the one document meant to settle
            # exactly that question.
            score_txt += " — marks recorded, but the attempt was disqualified; no result stands"
        elif pass_mark is not None:
            score_txt += f" — pass mark {pass_mark}%, {'PASS' if pct >= pass_mark else 'FAIL'}"
    else:
        score_txt = "Not graded"

    story.append(Paragraph("Attempt", styles["Heading2"]))
    story.append(facts_table([
        ["Started", _fmt_dt(submission.started_at)],
        ["Completed", _fmt_dt(submission.completed_at)],
        ["Duration", duration],
        ["Result", score_txt],
        ["Status", submission.status_label()],
        ["Termination reason", submission.termination_reason or "—"],
    ]))
    story.append(Spacer(1, 14))

    # ------------------------------------------------------------- integrity
    violations = sorted(submission.violations, key=lambda v: v.timestamp or datetime.min)
    severity = proctoring.total_severity(submission)
    limit = proctoring.max_violations_for(exam)

    story.append(Paragraph("Integrity summary", styles["Heading2"]))
    story.append(facts_table([
        ["Violations recorded", str(len(violations))],
        ["Weighted severity", f"{severity} of {limit} permitted"],
        ["Outcome", "Disqualified — severity limit reached" if submission.disqualified
                    else ("Locked by proctor" if submission.locked else "Completed within limits")],
    ], label_bg="#7f1d1d" if submission.disqualified else "#334155"))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Severity is weighted by violation type — probing developer tools counts for more "
        "than a single stray keystroke. An attempt is terminated automatically when the "
        "weighted total reaches the permitted limit.", small,
    ))
    story.append(Spacer(1, 14))

    # ------------------------------------------------------- violation table
    story.append(Paragraph("Violation timeline", styles["Heading2"]))
    if violations:
        rows = [["#", "Time (UTC)", "Into exam", "Type", "Sev", "Detail"]]
        for i, v in enumerate(violations, 1):
            rows.append([
                str(i),
                _fmt_dt(v.timestamp),
                _fmt_offset(v.timestamp, submission.started_at),
                Paragraph(v.violation_type or "—", cell),
                # Show the weight that actually counted, which is derived from
                # the type at scoring time (proctoring.total_severity does the
                # same). The stored `severity` column can be a stale default
                # from an older row, and printing it here made the rows fail to
                # add up to the weighted total stated above.
                str(proctoring.severity_for(v.violation_type)),
                Paragraph(v.details or "—", cell),
            ])
        table = Table(rows, repeatRows=1,
                      colWidths=[0.9 * cm, 3.2 * cm, 2.0 * cm, 4.0 * cm, 1.0 * cm, 5.5 * cm])
        story.append(header_style(table, "#b91c1c"))
    else:
        story.append(Paragraph("No violations were recorded during this attempt.", styles["Normal"]))
    story.append(Spacer(1, 16))

    # --------------------------------------------------------- contact sheet
    snaps = sorted(submission.snapshots, key=lambda s: s.captured_at or datetime.min)
    chosen = _select_snapshots(snaps)

    story.append(PageBreak())
    story.append(Paragraph("Webcam evidence", styles["Heading2"]))
    if not snaps:
        story.append(Paragraph(
            "No webcam stills were captured for this attempt. Either webcam proctoring was "
            "switched off for this exam, or the candidate's camera was unavailable — the "
            "violation timeline above records the latter.", styles["Normal"],
        ))
    else:
        note = f"{len(snaps)} still(s) captured."
        if len(chosen) < len(snaps):
            note += (f" Showing {len(chosen)}: every flagged frame, plus an evenly spaced "
                     f"sample across the attempt. The full set remains in the system.")
        story.append(Paragraph(note, small))
        story.append(Spacer(1, 10))

        width = SNAP_WIDTH_CM * cm
        row_imgs, row_caps = [], []
        grid = []

        def flush_row():
            if not row_imgs:
                return
            while len(row_imgs) < SNAPS_PER_ROW:
                row_imgs.append("")
                row_caps.append("")
            grid.append(list(row_imgs))
            grid.append(list(row_caps))
            row_imgs.clear()
            row_caps.clear()

        for snap in chosen:
            img = _snapshot_flowable(snap, width, Image)
            if img is None:
                continue
            label = _fmt_dt(snap.captured_at)
            offset = _fmt_offset(snap.captured_at, submission.started_at)
            text = f"{label}<br/>{offset} into exam"
            if snap.flagged:
                text = f'<b><font color="#b91c1c">FLAGGED</font></b><br/>{text}'
            row_imgs.append(img)
            row_caps.append(Paragraph(text, caption))
            if len(row_imgs) == SNAPS_PER_ROW:
                flush_row()
        flush_row()

        if grid:
            sheet = Table(grid, colWidths=[width + 0.3 * cm] * SNAPS_PER_ROW)
            sheet.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(sheet)
        else:
            story.append(Paragraph(
                "Stills were recorded but could not be read back from storage.", styles["Normal"]))

    # ---------------------------------------------------------------- footer
    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "This report is a factual record of what the proctoring system observed and the "
        "thresholds it applied. It does not by itself establish intent or misconduct; that "
        "determination rests with the examination authority.", small,
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer
