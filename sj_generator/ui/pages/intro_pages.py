from __future__ import annotations

from pathlib import Path
import random

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPixmap, QTextBlockFormat, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from sj_generator.ui.constants import PAGE_WELCOME
from sj_generator.paths import app_paths

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def _quotation_dir() -> Path:
    return app_paths(Path(__file__).resolve().parents[3]).reference_quotation_dir


def _find_quote_file() -> Path | None:
    quote_dir = _quotation_dir()
    if not quote_dir.exists():
        return None
    txt_files = sorted(path for path in quote_dir.iterdir() if path.is_file() and path.suffix.lower() == ".txt")
    return txt_files[0] if txt_files else None


def _find_photo_file(author_name: str) -> Path | None:
    picture_dir = app_paths(Path(__file__).resolve().parents[3]).reference_picture_dir
    if not picture_dir.exists():
        return None

    normalized_author = author_name.strip()
    if normalized_author:
        for suffix in _IMAGE_SUFFIXES:
            candidate = picture_dir / f"{normalized_author}{suffix}"
            if candidate.exists():
                return candidate

    image_files = sorted(path for path in picture_dir.iterdir() if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES)
    return image_files[0] if image_files else None


def _load_quote_content() -> tuple[str, str, Path | None]:
    quote_file = _find_quote_file()
    if quote_file is None:
        return "名言文件缺失。请在 reference/quotation 目录放入 .txt 文件。", "未提供作者", None

    lines = [line.strip() for line in quote_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    quote_text = random.choice(lines) if lines else "名言文件为空。"
    author_name = quote_file.stem.strip() or "未提供作者"
    return quote_text, author_name, _find_photo_file(author_name)


class IntroPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("登录")

        quote_text, author_name, photo_path = _load_quote_content()
        self._photo_path = photo_path

        self._photo_label = QLabel()
        self._photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._photo_label.setMinimumSize(300, 380)
        self._photo_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._photo_label.setStyleSheet("border: 1px solid #d9d9d9; background: #f7f7f7;")
        self._apply_photo()

        quote_label = QTextBrowser()
        quote_label.setReadOnly(True)
        quote_label.setFrameShape(QTextBrowser.Shape.NoFrame)
        quote_label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        quote_label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        quote_label.setStyleSheet(
            "background: transparent; border: none;"
        )
        quote_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        quote_label.document().setDocumentMargin(0)
        quote_font = QFont()
        quote_font.setFamilies(["KaiTi", "STKaiti", "SimSun", "Songti SC"])
        quote_font.setPointSize(26)
        quote_font.setWeight(QFont.Weight.DemiBold)
        quote_label.setFont(quote_font)

        cursor = QTextCursor(quote_label.document())
        block_format = QTextBlockFormat()
        block_format.setLineHeight(140.0, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        block_format.setTextIndent(float(QFontMetrics(quote_font).horizontalAdvance("　　")))
        char_format = QTextCharFormat()
        char_format.setFont(quote_font)
        char_format.setForeground(QColor("#c00000"))
        cursor.setBlockFormat(block_format)
        cursor.insertText(quote_text, char_format)

        author_label = QLabel(f"—— {author_name}")
        author_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        author_label.setStyleSheet(
            "font-size: 20px; color: #000000; "
            "font-family: 'KaiTi', 'STKaiti', 'SimSun', 'Songti SC';"
        )

        username_edit = QLineEdit()
        username_edit.setPlaceholderText("请输入用户名")
        username_edit.setMinimumHeight(36)

        password_edit = QLineEdit()
        password_edit.setPlaceholderText("请输入密码")
        password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        password_edit.setMinimumHeight(36)
        password_edit.returnPressed.connect(self._go_next)

        btn_style = "font-size: 16px; font-weight: 600; padding: 6px 18px;"

        user_login_btn = QPushButton("用户登录")
        user_login_btn.setMinimumHeight(44)
        user_login_btn.setStyleSheet(btn_style)
        user_login_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        user_login_btn.clicked.connect(self._go_next)

        local_login_btn = QPushButton("本地登录")
        local_login_btn.setMinimumHeight(44)
        local_login_btn.setStyleSheet(btn_style)
        local_login_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        local_login_btn.clicked.connect(self._go_next)

        exit_btn = QPushButton("退出程序")
        exit_btn.setMinimumHeight(44)
        exit_btn.setStyleSheet(btn_style)
        exit_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        exit_btn.clicked.connect(self._exit_program)

        login_form = QFormLayout()
        login_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        login_form.setFormAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        login_form.setHorizontalSpacing(12)
        login_form.setVerticalSpacing(12)
        login_form.addRow("用户名：", username_edit)
        login_form.addRow("密  码：", password_edit)

        login_panel = QFrame()
        login_panel.setStyleSheet("QFrame { border: none; background: #fafafa; }")
        login_layout = QVBoxLayout()
        login_layout.setContentsMargins(20, 18, 20, 18)
        login_layout.setSpacing(12)
        login_title = QLabel("思政智题云枢系统登陆")
        login_title.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        login_title.setStyleSheet("font-size: 18px; font-weight: 600;")
        login_layout.addWidget(login_title)
        login_layout.addLayout(login_form)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addWidget(user_login_btn)
        btn_row.addWidget(local_login_btn)
        btn_row.addWidget(exit_btn)
        login_layout.addLayout(btn_row)
        login_panel.setLayout(login_layout)

        photo_layout = QVBoxLayout()
        photo_layout.addWidget(self._photo_label, 1)

        quote_layout = QVBoxLayout()
        quote_layout.addStretch(1)
        quote_layout.addWidget(quote_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        quote_layout.addWidget(author_label)
        quote_layout.addStretch(1)

        right_layout = QVBoxLayout()
        right_layout.setSpacing(18)
        right_layout.addLayout(quote_layout, 6)
        right_layout.addWidget(login_panel, 4)

        content_layout = QHBoxLayout()
        content_layout.addLayout(photo_layout, 4)
        content_layout.addLayout(right_layout, 6)

        layout = QVBoxLayout()
        layout.addLayout(content_layout, 1)
        self.setLayout(layout)

    def initializePage(self) -> None:
        wizard = self.wizard()
        if wizard is not None:
            wizard.setButtonText(QWizard.WizardButton.NextButton, "下一步")
            wizard.button(QWizard.WizardButton.BackButton).hide()
            wizard.button(QWizard.WizardButton.NextButton).hide()
            wizard.button(QWizard.WizardButton.CancelButton).hide()

    def cleanupPage(self) -> None:
        wizard = self.wizard()
        if wizard is not None:
            wizard.button(QWizard.WizardButton.BackButton).show()
            wizard.button(QWizard.WizardButton.NextButton).show()
            wizard.button(QWizard.WizardButton.CancelButton).show()

    def nextId(self) -> int:
        return PAGE_WELCOME

    def _go_next(self) -> None:
        wizard = self.wizard()
        if wizard is not None:
            wizard.next()

    def _exit_program(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _apply_photo(self) -> None:
        if self._photo_path is None:
            self._photo_label.setText("暂无照片\n\n请将图片文件放入 reference/picture 目录\n并优先使用与作者同名的文件名。")
            return

        pixmap = QPixmap(str(self._photo_path))
        if pixmap.isNull():
            self._photo_label.setText("照片加载失败")
            return

        scaled = pixmap.scaled(
            300,
            380,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._photo_label.setPixmap(scaled)
