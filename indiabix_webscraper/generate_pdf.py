"""
IndiaBix Current Affairs — Category-wise PDF Generator
========================================================
Fetches all questions from the running API, groups them by category,
then by date (newest first), and produces one polished PDF per category
plus an optional combined master PDF.

Usage:
    python generate_pdf.py                        # all categories → ./pdfs/
    python generate_pdf.py --category "Science"   # single category
    python generate_pdf.py --month 2026-01        # filter by month
    python generate_pdf.py --out ./output         # custom output dir
    python generate_pdf.py --combined             # also build master PDF
    python generate_pdf.py --api http://host:8000 # custom API URL

Requirements:
    pip install reportlab requests
    pip install pypdf   (optional — only for --combined)
    API must be running: uvicorn api:app --port 8000
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ── Page geometry ──────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4
ML = 18 * mm   # margin left
MR = 18 * mm   # margin right
MT = 22 * mm   # margin top
MB = 18 * mm   # margin bottom
CW = PAGE_W - ML - MR   # usable content width

# ── Palette ────────────────────────────────────────────────────────────────────
CA      = colors.HexColor("#e07b00")   # accent orange
CA2     = colors.HexColor("#c0392b")   # red accent
CG      = colors.HexColor("#1a7a3c")   # correct green
CDARK   = colors.HexColor("#1a1e2a")   # dark header
CLIGHT  = colors.HexColor("#f5f6f8")   # light row
CMUTED  = colors.HexColor("#6b7280")   # muted text
CTEXT   = colors.HexColor("#1c1e26")   # body text
CBORDER = colors.HexColor("#dee2ea")   # border


# ── Helpers ────────────────────────────────────────────────────────────────────
def safe(text: str) -> str:
    return (str(text) if text else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def date_label(ds: str) -> str:
    try:
        return datetime.strptime(ds, "%Y-%m-%d").strftime("%A, %d %B %Y")
    except ValueError:
        return ds

def group_by_date(questions: list[dict]) -> dict[str, list[dict]]:
    d: dict[str, list[dict]] = defaultdict(list)
    for q in questions:
        d[q["date"]].append(q)
    return dict(sorted(d.items(), reverse=True))

def safe_filename(name: str) -> str:
    return "_".join("".join(c if c.isalnum() or c in " -" else "_" for c in name).split())


# ── Style sheet ────────────────────────────────────────────────────────────────
def styles() -> dict:
    def S(name, **kw):
        return ParagraphStyle(name, **kw)
    return {
        "cover_title": S("cover_title", fontName="Helvetica-Bold", fontSize=30,
                          leading=38, textColor=CTEXT, alignment=TA_CENTER),
        "cover_cat":   S("cover_cat", fontName="Helvetica-Bold", fontSize=19,
                          leading=24, textColor=CA, alignment=TA_CENTER),
        "cover_sub":   S("cover_sub", fontName="Helvetica", fontSize=11,
                          leading=15, textColor=CMUTED, alignment=TA_CENTER),
        "cover_meta":  S("cover_meta", fontName="Helvetica", fontSize=9.5,
                          leading=13, textColor=CMUTED, alignment=TA_CENTER),
        "date_lbl":    S("date_lbl", fontName="Helvetica-Bold", fontSize=10.5,
                          leading=14, textColor=colors.white),
        "date_cnt":    S("date_cnt", fontName="Helvetica", fontSize=9,
                          leading=12, textColor=CMUTED, alignment=TA_RIGHT),
        "q_text":      S("q_text", fontName="Helvetica-Bold", fontSize=10.5,
                          leading=15, textColor=CTEXT),
        "q_num":       S("q_num", fontName="Helvetica-Bold", fontSize=8.5,
                          leading=11, textColor=colors.white, alignment=TA_CENTER),
        "opt_norm":    S("opt_norm", fontName="Helvetica", fontSize=9.5,
                          leading=13, textColor=CTEXT, leftIndent=4),
        "opt_corr":    S("opt_corr", fontName="Helvetica-Bold", fontSize=9.5,
                          leading=13, textColor=CG, leftIndent=4),
        "expl_lbl":    S("expl_lbl", fontName="Helvetica-Bold", fontSize=8.5,
                          leading=11, textColor=CA, spaceAfter=2),
        "expl_txt":    S("expl_txt", fontName="Helvetica", fontSize=9,
                          leading=13, textColor=CTEXT, alignment=TA_JUSTIFY),
        "ans_txt":     S("ans_txt", fontName="Helvetica", fontSize=9,
                          leading=12, textColor=CTEXT),
    }


# ── Page header/footer callback ────────────────────────────────────────────────
def make_hf(cat_name: str):
    def hf(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(CA)
        canvas.rect(0, PAGE_H - 7 * mm, PAGE_W, 3, fill=1, stroke=0)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawString(ML, PAGE_H - 14 * mm, cat_name.upper())
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(CMUTED)
        canvas.drawRightString(PAGE_W - MR, PAGE_H - 14 * mm, "IndiaBix Current Affairs")
        canvas.setStrokeColor(CBORDER)
        canvas.setLineWidth(0.4)
        canvas.line(ML, MB - 2, PAGE_W - MR, MB - 2)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(ML, MB - 10, "Practice Worksheet")
        canvas.drawRightString(PAGE_W - MR, MB - 10, f"Page {doc.page}")
        canvas.restoreState()
    return hf


# ── Cover ──────────────────────────────────────────────────────────────────────
def cover(cat: str, n_q: int, n_d: int, month: str | None, ST: dict) -> list:
    elems = [Spacer(1, 55 * mm)]
    bar = Table([[""]], colWidths=[PAGE_W], rowHeights=[5],
                style=TableStyle([("BACKGROUND", (0,0), (-1,-1), CA)]))
    elems += [bar, Spacer(1, 18 * mm)]
    elems += [Paragraph("IndiaBix Current Affairs", ST["cover_sub"]), Spacer(1, 5 * mm)]
    elems += [Paragraph(safe(cat), ST["cover_cat"]), Spacer(1, 4 * mm)]
    elems += [Paragraph("Category Practice Worksheet", ST["cover_title"]), Spacer(1, 14 * mm)]
    now = datetime.now().strftime("%d %B %Y")
    period = month if month else "All Available Dates"
    for line in [f"Total Questions: {n_q}", f"Dates Covered: {n_d}",
                 f"Period: {period}", f"Generated on: {now}"]:
        elems += [Paragraph(line, ST["cover_meta"]), Spacer(1, 3 * mm)]
    elems += [Spacer(1, 18 * mm),
              Table([[""]], colWidths=[PAGE_W], rowHeights=[3],
                    style=TableStyle([("BACKGROUND", (0,0), (-1,-1), CA2)]))]
    return elems


# ── Date section header ────────────────────────────────────────────────────────
def date_header(ds: str, n: int, ST: dict) -> list:
    row = Table(
        [[Paragraph(f"  {date_label(ds)}", ST["date_lbl"]),
          Paragraph(f'{n} question{"s" if n!=1 else ""}  ', ST["date_cnt"])]],
        colWidths=[CW * 0.72, CW * 0.28],
        style=TableStyle([
            ("BACKGROUND", (0,0), (0,0), CDARK),
            ("BACKGROUND", (1,0), (1,0), CLIGHT),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 7),
            ("BOTTOMPADDING", (0,0), (-1,-1), 7),
            ("LEFTPADDING", (0,0), (0,0), 8),
            ("RIGHTPADDING", (1,0), (1,0), 8),
            ("ROUNDEDCORNERS", [5]),
        ]),
    )
    return [Spacer(1, 4 * mm), row, Spacer(1, 5 * mm)]


# ── Question block ─────────────────────────────────────────────────────────────
def question_block(q: dict, num: int, ST: dict) -> list:
    elems = []

    # Q-number badge + question text
    badge = Table([[Paragraph(f"Q{num}", ST["q_num"])]],
                  colWidths=[7.5 * mm], rowHeights=[7.5 * mm],
                  style=TableStyle([
                      ("BACKGROUND", (0,0), (-1,-1), CA),
                      ("ALIGN", (0,0), (-1,-1), "CENTER"),
                      ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                      ("ROUNDEDCORNERS", [3]),
                      ("LEFTPADDING", (0,0), (-1,-1), 0),
                      ("RIGHTPADDING", (0,0), (-1,-1), 0),
                      ("TOPPADDING", (0,0), (-1,-1), 0),
                      ("BOTTOMPADDING", (0,0), (-1,-1), 0),
                  ]))
    q_para = Paragraph(safe(q.get("question", "")), ST["q_text"])
    header = Table([[badge, q_para]],
                   colWidths=[10 * mm, CW - 10 * mm],
                   style=TableStyle([
                       ("VALIGN", (0,0), (-1,-1), "TOP"),
                       ("LEFTPADDING", (0,0), (-1,-1), 0),
                       ("RIGHTPADDING", (0,0), (-1,-1), 0),
                       ("TOPPADDING", (0,0), (-1,-1), 0),
                       ("BOTTOMPADDING", (0,0), (-1,-1), 0),
                       ("RIGHTPADDING", (0,0), (0,0), 3 * mm),
                   ]))
    elems += [header, Spacer(1, 3 * mm)]

    # Options
    opts = q.get("options", {})
    correct = q.get("correct_answer", "")
    opt_rows = []
    style_cmds = [
        ("FONTSIZE", (0,0), (-1,-1), 9.5),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING", (0,0), (0,-1), 3 * mm),
        ("RIGHTPADDING", (0,0), (-1,-1), 2 * mm),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("GRID", (0,0), (-1,-1), 0.3, CBORDER),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [CLIGHT, colors.white]),
    ]
    for i, (lbl, txt) in enumerate(sorted(opts.items())):
        is_c = lbl == correct
        lbl_para = Paragraph(
            f'<font color="white"><b>{lbl}</b></font>',
            ParagraphStyle("ol", fontName="Helvetica-Bold", fontSize=9, leading=11, alignment=TA_CENTER),
        )
        lbl_cell = Table([[lbl_para]], colWidths=[5.5 * mm], rowHeights=[5.5 * mm],
                         style=TableStyle([
                             ("BACKGROUND", (0,0), (-1,-1), CG if is_c else CDARK),
                             ("ALIGN", (0,0), (-1,-1), "CENTER"),
                             ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                             ("ROUNDEDCORNERS", [3]),
                             ("LEFTPADDING", (0,0), (-1,-1), 0),
                             ("RIGHTPADDING", (0,0), (-1,-1), 0),
                             ("TOPPADDING", (0,0), (-1,-1), 0),
                             ("BOTTOMPADDING", (0,0), (-1,-1), 0),
                         ]))
        if is_c:
            txt_para = Paragraph(f'<font color="#1a7a3c"><b>&#10003; {safe(txt)}</b></font>',
                                 ParagraphStyle("oc", fontName="Helvetica-Bold", fontSize=9.5,
                                                leading=13, textColor=CG, leftIndent=4))
            style_cmds += [
                ("BACKGROUND", (0,i), (-1,i), colors.HexColor("#edfbf1")),
                ("TEXTCOLOR", (0,i), (-1,i), CG),
            ]
        else:
            txt_para = Paragraph(safe(txt), ST["opt_norm"])

        opt_rows.append([lbl_cell, txt_para])

    if opt_rows:
        opts_tbl = Table(opt_rows, colWidths=[8.5 * mm, CW - 8.5 * mm],
                         style=TableStyle(style_cmds))
        elems += [opts_tbl, Spacer(1, 2.5 * mm)]

    # Correct answer summary bar
    ans_txt = q.get("correct_answer_text", opts.get(correct, ""))
    ans_para = Paragraph(
        f'<b>Correct Answer: </b><font color="#1a7a3c"><b>{safe(correct)} — {safe(ans_txt)}</b></font>',
        ParagraphStyle("ab", fontName="Helvetica", fontSize=9, leading=12, textColor=CTEXT),
    )
    ans_bar = Table([[ans_para]], colWidths=[CW],
                    style=TableStyle([
                        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#edfbf1")),
                        ("LEFTPADDING", (0,0), (-1,-1), 8),
                        ("RIGHTPADDING", (0,0), (-1,-1), 8),
                        ("TOPPADDING", (0,0), (-1,-1), 5),
                        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                        ("LINEAFTER", (0,0), (0,-1), 3, CG),
                        ("LINEBEFORE", (0,0), (0,0), 3, CG),
                        ("ROUNDEDCORNERS", [4]),
                    ]))
    elems += [ans_bar]

    # Explanation
    expl = (q.get("explanation") or "").strip()
    if expl:
        expl_tbl = Table(
            [[Paragraph(safe(expl), ST["expl_txt"])]],
            colWidths=[CW],
            style=TableStyle([
                ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#fdf8f0")),
                ("LEFTPADDING", (0,0), (-1,-1), 10),
                ("RIGHTPADDING", (0,0), (-1,-1), 10),
                ("TOPPADDING", (0,0), (-1,-1), 7),
                ("BOTTOMPADDING", (0,0), (-1,-1), 7),
                ("LINEBEFORE", (0,0), (0,-1), 3, CA),
                ("ROUNDEDCORNERS", [4]),
            ]),
        )
        elems += [Spacer(1, 2 * mm),
                  Paragraph("Explanation", ST["expl_lbl"]),
                  expl_tbl]

    elems += [Spacer(1, 5 * mm),
              HRFlowable(width=CW, thickness=0.4, color=CBORDER),
              Spacer(1, 5 * mm)]
    return elems


# ── Build one category PDF ─────────────────────────────────────────────────────
def build_pdf(cat: str, questions: list[dict], out_path: Path, month: str | None):
    by_date = group_by_date(questions)
    ST = styles()

    doc = BaseDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=ML, rightMargin=MR,
        topMargin=MT + 8 * mm, bottomMargin=MB + 4 * mm,
        title=f"IndiaBix — {cat}",
        author="IndiaBix PDF Generator",
        subject=f"Current Affairs MCQs: {cat}",
    )

    cover_frame = Frame(0, 0, PAGE_W, PAGE_H, id="cover",
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    content_frame = Frame(ML, MB, CW, PAGE_H - MT - MB - 8 * mm, id="main",
                          leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame]),
        PageTemplate(id="content", frames=[content_frame], onPage=make_hf(cat)),
    ])

    story = []
    story += cover(cat, len(questions), len(by_date), month, ST)
    story += [NextPageTemplate("content"), PageBreak()]

    q_num = 1
    for date_str, qs in by_date.items():
        dh = date_header(date_str, len(qs), ST)
        first_q_block = question_block(qs[0], q_num, ST)
        story.append(KeepTogether(dh + first_q_block))
        q_num += 1
        for q in qs[1:]:
            story.append(KeepTogether(question_block(q, q_num, ST)))
            q_num += 1

    doc.build(story)


# ── API calls ──────────────────────────────────────────────────────────────────
def fetch_categories(base: str) -> list[dict]:
    r = requests.get(f"{base}/categories", timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_by_category(base: str, cat: str, month: str | None) -> list[dict]:
    params = {"newest_first": "true"}
    if month:
        params["month"] = month
    url = f"{base}/categories/{requests.utils.quote(cat, safe='')}/questions"
    r = requests.get(url, params=params, timeout=120)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json()


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Generate category-wise PDFs from IndiaBix API")
    p.add_argument("--category", default=None, help="Single category name to process")
    p.add_argument("--month", default=None, help="Month filter YYYY-MM")
    p.add_argument("--out", default="./pdfs", help="Output directory (default: ./pdfs)")
    p.add_argument("--combined", action="store_true", help="Merge all PDFs into one master file")
    p.add_argument("--api", default="http://localhost:8000", help="API base URL")
    args = p.parse_args()

    base = args.api.rstrip("/")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Verify API is alive
    try:
        stats = requests.get(f"{base}/stats", timeout=5).json()
        print(f"API OK — {stats['total_questions']} questions, "
              f"{stats['total_categories']} categories")
    except Exception as e:
        print(f"ERROR: Cannot reach API at {base}\n{e}")
        sys.exit(1)

    # Pick categories
    if args.category:
        cats = [{"category": args.category}]
    else:
        cats = fetch_categories(base)
        print(f"Processing {len(cats)} categories…\n")

    generated: list[Path] = []

    for ci, cat_info in enumerate(cats, 1):
        cat = (cat_info.get("category") or "").strip()
        if not cat or cat.lower() in ("none", "null", ""):
            continue

        n_total = cat_info.get("question_count", "?")
        print(f"[{ci}/{len(cats)}] {cat}  ({n_total} total)")

        questions = fetch_by_category(base, cat, args.month)
        if not questions:
            print("       → No questions — skipped")
            continue

        suffix = f"_{args.month}" if args.month else ""
        out_path = out_dir / f"{safe_filename(cat)}{suffix}.pdf"
        print(f"       → {len(questions)} questions across "
              f"{len(set(q['date'] for q in questions))} dates")

        try:
            build_pdf(cat, questions, out_path, args.month)
            size_kb = out_path.stat().st_size // 1024
            print(f"       ✓ {out_path.name}  ({size_kb} KB)")
            generated.append(out_path)
        except Exception as exc:
            print(f"       ✗ Error: {exc}")
            import traceback; traceback.print_exc()

    # Optional combined
    if args.combined and len(generated) > 1:
        print("\nMerging all PDFs into master file…")
        try:
            from pypdf import PdfWriter, PdfReader
            writer = PdfWriter()
            for pdf in generated:
                for page in PdfReader(str(pdf)).pages:
                    writer.add_page(page)
            suffix = f"_{args.month}" if args.month else ""
            combo = out_dir / f"IndiaBix_All_Categories{suffix}.pdf"
            with open(combo, "wb") as f:
                writer.write(f)
            print(f"✓ Combined PDF: {combo}  ({combo.stat().st_size//1024} KB)")
        except ImportError:
            print("pypdf not installed. Run: pip install pypdf")
        except Exception as exc:
            print(f"ERROR merging: {exc}")

    print(f"\n{'─'*52}")
    print(f"Done. {len(generated)} PDF(s) written to: {out_dir.resolve()}")
    for f in generated:
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
    