"""Generate the one-sided reference-range OCR fixture (issue #159).

Produces ``tests/fixtures/one_sided_reference_ranges_lab.pdf``: a clean,
synthetic laboratory report whose printed reference ranges include one-sided
upper limits (``< X`` / ``≤ X``), one-sided lower limits (``> X`` / ``≥ X``)
and two-sided controls. The exact reproduction from issue #159 is the first
row: ``Glucose, Fasting 15.0 HIGH mmol/L < 10.0``.

Expected ingestion result per row (post-fix contract):

- ``< X`` / ``≤ X`` -> ``reference_range_max = X``, min null, never min=X.
- ``> X`` / ``≥ X`` -> ``reference_range_min = X``, max null.
- The value/flag pair must stay consistent: HIGH sits above the range, LOW
  below it. The ``hs-CRP`` row prints no flag, so it exercises the prompt
  convention alone (the write-time sanitizer only corrects an inverted bound
  when an interpretation flag corroborates it).

The PDF is image-backed on purpose: ingestion rasterises PDFs before OCR
(``app.ai.processors.ocr.utils``), so this is the same shape the pipeline
sees from a scanned lab report while staying deterministic and dependency-free.

Usage (from ``backend/``):

    python scripts/generate_one_sided_reference_pdf.py
    python scripts/generate_one_sided_reference_pdf.py --out /tmp/lab.pdf

Requires only Pillow, which is already a backend dependency.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROWS = [
    ("Glucose, Fasting", "15.0", "HIGH", "mmol/L", "< 10.0"),
    ("Total Cholesterol", "7.8", "HIGH", "mmol/L", "< 5.2"),
    ("LDL Cholesterol", "4.9", "HIGH", "mmol/L", "< 3.4"),
    ("Triglycerides", "2.9", "HIGH", "mmol/L", "≤ 1.7"),
    ("HDL Cholesterol", "0.6", "LOW", "mmol/L", "> 1.0"),
    ("eGFR (CKD-EPI)", "45", "LOW", "mL/min/1.73", "≥ 60"),
    ("Hemoglobin", "148", "NORMAL", "g/L", "130 - 175"),
    ("Potassium", "6.2", "HIGH", "mmol/L", "3.5 - 5.1"),
    ("Creatinine", "42", "LOW", "µmol/L", "60 – 110"),  # noqa: RUF001 -- en dash is realistic lab typography
    ("hs-CRP", "12.0", "", "mg/L", "< 5.0"),
]

COLUMNS = (
    ("TEST", 48.0, "left"),
    ("RESULT", 300.0, "right"),
    ("FLAG", 345.0, "left"),
    ("UNIT", 420.0, "left"),
    ("REF. RANGE", 490.0, "left"),
)

PAGE_WIDTH_PT = 595.0
PAGE_HEIGHT_PT = 842.0
DPI = 300
SCALE = DPI / 72.0

BLACK = "#111111"
DARK_GREY = "#374151"
MID_GREY = "#4b5563"
RULE_GREY = "#9ca3af"
LIGHT_GREY = "#6b7280"

_FONT_CANDIDATES = {
    "regular": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ),
    "bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
    ),
}


def _load_font(weight: str, size_pt: float) -> ImageFont.FreeTypeFont:
    px = round(size_pt * SCALE)
    for candidate in _FONT_CANDIDATES[weight]:
        try:
            return ImageFont.truetype(candidate, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


def _render(out_path: Path) -> None:
    width = round(PAGE_WIDTH_PT * SCALE)
    height = round(PAGE_HEIGHT_PT * SCALE)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    def px(value: float) -> int:
        return round(value * SCALE)

    def text(
        x: float,
        y: float,
        value: str,
        size: float = 10.0,
        weight: str = "regular",
        anchor: str = "lt",
        color: str = BLACK,
    ) -> None:
        draw.text((px(x), px(y)), value, font=_load_font(weight, size), fill=color, anchor=anchor)

    def rule(y: float, x0: float = 48.0, x1: float = 555.0, width_pt: float = 0.7) -> None:
        draw.line(
            [(px(x0), px(y)), (px(x1), px(y))],
            fill=RULE_GREY,
            width=max(1, px(width_pt)),
        )

    def wrapped(value: str, size: float, max_width: float) -> list[str]:
        font = _load_font("regular", size)
        lines: list[str] = []
        current = ""
        for word in value.split():
            candidate = f"{current} {word}".strip()
            if current and draw.textlength(candidate, font=font) > px(max_width):
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    text(48, 70, "NORTHGATE CLINICAL LABORATORY", size=17, weight="bold")
    text(
        48,
        96,
        "Synthetic laboratory report — ingestion test fixture for one-sided reference ranges",
        size=9,
        color=MID_GREY,
    )
    rule(116, width_pt=1.1)

    text(48, 136, "PATIENT (SYNTHETIC)", size=8, weight="bold", color=MID_GREY)
    text(48, 154, "Name: Test Patient", size=10)
    text(48, 174, "Date of Birth: 12 May 1985", size=10)
    text(48, 194, "Sex: Male", size=10)

    text(325, 136, "SPECIMEN", size=8, weight="bold", color=MID_GREY)
    text(325, 154, "Report ID: SYNTH-OSR-001", size=10)
    text(325, 174, "Collected: 18 Nov 2026", size=10)
    text(325, 194, "Fasting: Yes (12 h)", size=10)

    rule(216)

    text(48, 240, "BIOMARKERS — REFERENCE RANGES AS PRINTED", size=11, weight="bold")
    text(
        48,
        262,
        "Upper limit = value preceded by  <  or  ≤       Lower limit = value preceded by  >  or  ≥",
        size=8.5,
        color=MID_GREY,
    )

    for label, x, align in COLUMNS:
        anchor = "rs" if align == "right" else "ls"
        text(x, 308, label, size=8.5, weight="bold", anchor=anchor, color=DARK_GREY)
    rule(318, width_pt=0.9)

    row_y = 340.0
    row_step = 31.5
    for index, (name, result, flag, unit, reference) in enumerate(ROWS):
        if index:
            rule(row_y - 9, width_pt=0.35)
        weight = "bold" if flag in {"HIGH", "LOW"} else "regular"
        baseline = row_y + 10.0
        text(48, baseline, name, size=10, weight=weight, anchor="ls")
        text(300, baseline, result, size=10, weight=weight, anchor="rs")
        text(345, baseline, flag, size=9.5, weight="bold", anchor="ls")
        text(420, baseline, unit, size=9.5, anchor="ls")
        text(490, baseline, reference, size=10, weight="bold", anchor="ls")
        row_y += row_step

    rule(row_y - 9, width_pt=0.9)

    notes_y = row_y + 10
    text(48, notes_y, "NOTES", size=8, weight="bold", color=MID_GREY)
    notes = (
        "1. Reference ranges are transcribed exactly as printed by the laboratory; "
        "a one-sided limit has no opposite bound.",
        "2. Values marked HIGH must sit above the upper limit; values marked LOW must "
        "sit below the lower limit.",
        "3. The hs-CRP row intentionally prints no flag: its one-sided range must still "
        "be assigned to the upper side.",
    )
    cursor = notes_y + 18
    for note in notes:
        for line in wrapped(note, 8.5, 505):
            text(48, cursor, line, size=8.5, color=DARK_GREY)
            cursor += 12.5
        cursor += 2

    rule(772, width_pt=0.9)
    text(
        48,
        782,
        "SYNTHETIC DATA — no real patient. Generated by "
        "scripts/generate_one_sided_reference_pdf.py for Health Assistant ingestion testing.",
        size=8,
        color=LIGHT_GREY,
    )

    image.save(out_path, format="PDF", resolution=float(DPI))


def main() -> None:
    default = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / ("one_sided_reference_ranges_lab.pdf")
    )
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=default, help=f"output PDF (default: {default})"
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    _render(args.out)
    print(args.out)


if __name__ == "__main__":
    main()
