from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pygments import lex
from pygments.lexers import PythonLexer
from pygments.token import Comment, Keyword, Literal, Name, Number, Operator, String, Text, Token
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = PROJECT_ROOT / "docs" / "项目源码.pdf"
PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT = 24
RIGHT = 24
TOP = 26
BOTTOM = 24
HEADER_HEIGHT = 26
LINE_NUMBER_WIDTH = 34
PADDING_X = 8
BODY_SIZE = 8.2
LINE_GAP = 10.6
TITLE_SIZE = 12
FONT_ASCII = "CodeLatin"
FONT_ASCII_BOLD = "CodeLatinBold"
FONT_CJK = "STSong-Light"
CODE_WIDTH = PAGE_WIDTH - LEFT - RIGHT - LINE_NUMBER_WIDTH - PADDING_X * 2
CODE_LEFT = LEFT + LINE_NUMBER_WIDTH + PADDING_X
LINE_Y_START = PAGE_HEIGHT - TOP - HEADER_HEIGHT - 16
COLORS = {
    "page_bg": colors.HexColor("#FFFFFF"),
    "header_bg": colors.HexColor("#EAF2FF"),
    "header_text": colors.HexColor("#1F3B6D"),
    "code_bg": colors.HexColor("#FAFBFC"),
    "line_bg": colors.HexColor("#F3F4F6"),
    "line_text": colors.HexColor("#6B7280"),
    "border": colors.HexColor("#D0D7DE"),
    "default": colors.HexColor("#24292F"),
    "keyword": colors.HexColor("#8250DF"),
    "name": colors.HexColor("#24292F"),
    "function": colors.HexColor("#0969DA"),
    "class": colors.HexColor("#953800"),
    "decorator": colors.HexColor("#BC4C00"),
    "string": colors.HexColor("#0A7A33"),
    "number": colors.HexColor("#0550AE"),
    "comment": colors.HexColor("#6A737D"),
    "operator": colors.HexColor("#CF222E"),
}


@dataclass
class StyledChar:
    char: str
    font_name: str
    color: colors.Color


def register_fonts() -> None:
    fonts_dir = Path(r"C:\Windows\Fonts")
    pdfmetrics.registerFont(TTFont(FONT_ASCII, str(fonts_dir / "consola.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_ASCII_BOLD, str(fonts_dir / "consolab.ttf")))
    pdfmetrics.registerFont(UnicodeCIDFont(FONT_CJK))


def iter_source_files() -> list[Path]:
    files = [PROJECT_ROOT / "main.py"]
    files.extend(sorted((PROJECT_ROOT / "sj_generator").rglob("*.py")))
    return files


def color_for_token(token_type: Token) -> colors.Color:
    if token_type in Keyword:
        return COLORS["keyword"]
    if token_type in Name.Function:
        return COLORS["function"]
    if token_type in Name.Class:
        return COLORS["class"]
    if token_type in Name.Decorator:
        return COLORS["decorator"]
    if token_type in String:
        return COLORS["string"]
    if token_type in Number or token_type in Literal:
        return COLORS["number"]
    if token_type in Comment:
        return COLORS["comment"]
    if token_type in Operator:
        return COLORS["operator"]
    if token_type in Name:
        return COLORS["name"]
    return COLORS["default"]


def font_for_char(char: str, bold: bool = False) -> str:
    if ord(char) < 128:
        return FONT_ASCII_BOLD if bold else FONT_ASCII
    return FONT_CJK


def tokenized_lines(text: str) -> list[list[StyledChar]]:
    lines: list[list[StyledChar]] = [[]]
    lexer = PythonLexer()
    for token_type, value in lex(text.expandtabs(4), lexer):
        color = color_for_token(token_type)
        for char in value:
            if char == "\n":
                lines.append([])
                continue
            lines[-1].append(StyledChar(char=char, font_name=font_for_char(char), color=color))
    return lines


def wrap_styled_chars(styled_chars: list[StyledChar]) -> list[list[StyledChar]]:
    if not styled_chars:
        return [[]]
    wrapped: list[list[StyledChar]] = []
    current: list[StyledChar] = []
    width = 0.0
    for item in styled_chars:
        char_width = pdfmetrics.stringWidth(item.char, item.font_name, BODY_SIZE)
        if current and width + char_width > CODE_WIDTH:
            wrapped.append(current)
            current = []
            width = 0.0
        current.append(item)
        width += char_width
    if current or not wrapped:
        wrapped.append(current)
    return wrapped


def merge_runs(styled_chars: list[StyledChar]) -> list[tuple[str, str, colors.Color]]:
    if not styled_chars:
        return [("", FONT_ASCII, COLORS["default"])]
    runs: list[tuple[str, str, colors.Color]] = []
    text = styled_chars[0].char
    font_name = styled_chars[0].font_name
    color = styled_chars[0].color
    for item in styled_chars[1:]:
        if item.font_name == font_name and item.color == color:
            text += item.char
            continue
        runs.append((text, font_name, color))
        text = item.char
        font_name = item.font_name
        color = item.color
    runs.append((text, font_name, color))
    return runs


def draw_page_shell(pdf: canvas.Canvas, title: str, page_label: str) -> None:
    pdf.setFillColor(COLORS["page_bg"])
    pdf.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)

    header_y = PAGE_HEIGHT - TOP - HEADER_HEIGHT
    pdf.setFillColor(COLORS["header_bg"])
    pdf.roundRect(LEFT, header_y, PAGE_WIDTH - LEFT - RIGHT, HEADER_HEIGHT, 6, stroke=0, fill=1)

    pdf.setFont(FONT_ASCII_BOLD, TITLE_SIZE)
    pdf.setFillColor(COLORS["header_text"])
    pdf.drawString(LEFT + 8, header_y + 8, title)

    pdf.setFont(FONT_ASCII, BODY_SIZE)
    page_text_width = pdfmetrics.stringWidth(page_label, FONT_ASCII, BODY_SIZE)
    pdf.drawString(PAGE_WIDTH - RIGHT - 8 - page_text_width, header_y + 9, page_label)

    code_bottom = BOTTOM
    code_top = header_y - 8
    pdf.setFillColor(COLORS["code_bg"])
    pdf.rect(LEFT, code_bottom, PAGE_WIDTH - LEFT - RIGHT, code_top - code_bottom, stroke=0, fill=1)
    pdf.setFillColor(COLORS["line_bg"])
    pdf.rect(LEFT, code_bottom, LINE_NUMBER_WIDTH + PADDING_X, code_top - code_bottom, stroke=0, fill=1)
    pdf.setStrokeColor(COLORS["border"])
    pdf.line(LEFT + LINE_NUMBER_WIDTH + PADDING_X, code_bottom, LEFT + LINE_NUMBER_WIDTH + PADDING_X, code_top)


def draw_visual_line(pdf: canvas.Canvas, y: float, line_no: int | None, styled_chars: list[StyledChar]) -> float:
    if y < BOTTOM + LINE_GAP:
        return y

    if line_no is not None:
        line_text = f"{line_no:>4}"
        pdf.setFont(FONT_ASCII, BODY_SIZE)
        pdf.setFillColor(COLORS["line_text"])
        text_width = pdfmetrics.stringWidth(line_text, FONT_ASCII, BODY_SIZE)
        pdf.drawString(LEFT + LINE_NUMBER_WIDTH - text_width, y, line_text)

    x = CODE_LEFT
    for text, font_name, color in merge_runs(styled_chars):
        pdf.setFont(font_name, BODY_SIZE)
        pdf.setFillColor(color)
        pdf.drawString(x, y, text)
        x += pdfmetrics.stringWidth(text, font_name, BODY_SIZE)
    return y - LINE_GAP


def draw_source_file(pdf: canvas.Canvas, source_file: Path, file_index: int) -> None:
    relative_path = source_file.relative_to(PROJECT_ROOT).as_posix()
    lines = tokenized_lines(source_file.read_text(encoding="utf-8"))
    page_no = 1
    draw_page_shell(pdf, relative_path, f"{file_index:03d}-{page_no:02d}")
    y = LINE_Y_START

    for idx, line in enumerate(lines, start=1):
        wrapped = wrap_styled_chars(line)
        required_height = len(wrapped) * LINE_GAP
        if y - required_height < BOTTOM:
            pdf.showPage()
            page_no += 1
            draw_page_shell(pdf, relative_path, f"{file_index:03d}-{page_no:02d}")
            y = LINE_Y_START

        first = True
        for visual_line in wrapped:
            y = draw_visual_line(pdf, y, idx if first else None, visual_line)
            first = False


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    register_fonts()
    pdf = canvas.Canvas(str(OUTPUT_PATH), pagesize=A4)
    pdf.setTitle("项目源码")

    for file_index, source_file in enumerate(iter_source_files(), start=1):
        if file_index > 1:
            pdf.showPage()
        draw_source_file(pdf, source_file, file_index)

    pdf.save()
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
