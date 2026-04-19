from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QTextDocument
from PyQt6.QtPrintSupport import QPrinter


def export_markdown_to_pdf(markdown: str, pdf_path: Path) -> None:
    doc = QTextDocument()
    doc.setMarkdown(markdown)

    printer = QPrinter()
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(pdf_path))
    doc.print(printer)

