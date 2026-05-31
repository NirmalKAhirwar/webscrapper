import json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
)

styles = getSampleStyleSheet()


# -------------------------------------------------
# Company PDF
# -------------------------------------------------

def build_company_pdf(
        company_name,
        questions,
        output_file
):
    doc = SimpleDocTemplate(str(output_file))

    story = []

    story.append(
        Paragraph(
            f"<b>{company_name} Interview Questions</b>",
            styles["Title"]
        )
    )

    story.append(
        Paragraph(
            f"Total Questions: {len(questions)}",
            styles["Normal"]
        )
    )

    story.append(Spacer(1, 20))

    # TOC

    story.append(
        Paragraph(
            "<b>Table of Contents</b>",
            styles["Heading1"]
        )
    )

    story.append(Spacer(1, 10))

    for i, q in enumerate(questions, start=1):
        story.append(
            Paragraph(
                f"{i}. {q['question_name']}",
                styles["Normal"]
            )
        )

    story.append(PageBreak())

    # Questions

    for i, q in enumerate(questions, start=1):

        story.append(
            Paragraph(
                f"<b>Q{i}. {q['question_name']}</b>",
                styles["Heading2"]
            )
        )

        story.append(
            Paragraph(
                f"""
                <link href="{q['href']}">
                {q['href']}
                </link>
                """,
                styles["Normal"]
            )
        )

        story.append(Spacer(1, 15))

    doc.build(story)
# -------------------------------------------------
# Master PDF
# -------------------------------------------------
def build_master_pdf(
        company_data,
        output_file
):
    doc = SimpleDocTemplate(str(output_file))

    story = []

    story.append(
        Paragraph(
            "LeetCode Company Questions",
            styles["Title"]
        )
    )

    story.append(
        Paragraph(
            f"Companies: {len(company_data)}",
            styles["Normal"]
        )
    )

    story.append(PageBreak())

    # Decreasing Order

    story.append(
        Paragraph(
            "Company Questions in Decending Order",
            styles["Heading1"]
        )
    )

    ranked = sorted(
        company_data.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    for rank, (company, questions) in enumerate(
            ranked,
            start=1
    ):
        story.append(
            Paragraph(
                f"{rank}. {company} ({len(questions)} questions)",
                styles["Normal"]
            )
        )

    story.append(PageBreak())

    # TOC

    story.append(
        Paragraph(
            "Table of Contents",
            styles["Heading1"]
        )
    )

    for company, questions in ranked:
        story.append(
            Paragraph(
                f"{company} ({len(questions)})",
                styles["Normal"]
            )
        )

    story.append(PageBreak())

    # Companies

    for company, questions in ranked:

        story.append(
            Paragraph(
                company,
                styles["Title"]
            )
        )

        story.append(
            Paragraph(
                f"Questions: {len(questions)}",
                styles["Normal"]
            )
        )

        story.append(Spacer(1, 15))

        for idx, q in enumerate(
                questions,
                start=1
        ):
            story.append(
                Paragraph(
                    f"<b>{idx}. {q['question_name']}</b>",
                    styles["Normal"]
                )
            )

            story.append(
                Paragraph(
                    f"""
                    <link href="{q['href']}">
                    
                    </link>
                    """,
                    styles["Normal"]
                )
            )

            story.append(Spacer(1, 5))

        story.append(PageBreak())

    doc.build(story)


# -------------------------------------------------
# Generate Everything
# -------------------------------------------------
def generate_pdf(company_name: str | None = None):
    with open(
        "company_questions.json",
        "r",
        encoding="utf-8",
    ) as f:
        company_data = json.load(f)

    out_dir = Path("pdfs")
    out_dir.mkdir(exist_ok=True)

    ranked = sorted(
        company_data.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    )

    print(
        f"Generating PDFs for "
        f"{company_name or 'all companies'}..."
    )

    for company, questions in ranked:
        if not questions:
            continue

        if (
            company_name is not None
            and company.lower() != company_name.lower()
        ):
            continue

        filename = (
            company.replace("/", "_")
            .replace("\\", "_")
            + ".pdf"
        )

        build_company_pdf(
            company,
            questions,
            out_dir / filename,
        )

        print(f"Generated {filename}")
    print("\nDone.")



# generate_pdf()
# generate_pdf("Amazon")
