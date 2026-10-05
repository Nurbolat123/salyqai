"""Генерирует kaspi_business_2026_01.pdf — синтетическую PDF-выписку (данные вымышлены).

Те же операции, что в kaspi_business_2026_01.csv, но назначение платежа у
операции №101 обрезано, как бывает в PDF, а таблица разбита на две страницы с
повтором заголовка. Запуск: pip install reportlab && python make_kaspi_pdf.py
"""

import csv
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle

HERE = Path(__file__).parent
pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))

rows = list(csv.reader((HERE / "kaspi_business_2026_01.csv").open(encoding="utf-8"), delimiter=";"))
preamble = [r[0] for r in rows[:4]]
header, body = rows[5], [r for r in rows[6:] if r and r[0] and not r[0].startswith(("Итого", "не дата"))]
body = [r for i, r in enumerate(body) if i != 1]  # без дубля №101
body[0] = body[0][:8] + ["Оплата по счёту № 12"]  # обрезанное назначение

style = ParagraphStyle("p", fontName="DejaVu", fontSize=9)
table_style = TableStyle([
    ("FONT", (0, 0), (-1, -1), "DejaVu", 7),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
])
doc = SimpleDocTemplate(str(HERE / "kaspi_business_2026_01.pdf"), pagesize=landscape(A4))
story = [Paragraph(line, style) for line in preamble]
story += [Table([header] + body[:3], style=table_style), PageBreak(), Table([header] + body[3:], style=table_style)]
doc.build(story)
