from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWizard, QWizardPage
from sj_generator.ui.table_copy import CopyableTableWidget

from sj_generator.application.state import WizardState


class ImportSuccessPage(QWizardPage):
    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self._state = state
        self.setTitle("完成")
        self.setFinalPage(True)

        self._title = QLabel("导入数据库成功")
        self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._title.setStyleSheet("font-size: 22px; font-weight: 600;")

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        self._cost_table = CopyableTableWidget()
        self._cost_table.setColumnCount(5)
        self._cost_table.setHorizontalHeaderLabels(["厂商", "本次调用模型", "原本余额", "当前余额", "本次费用"])
        self._cost_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._cost_table.verticalHeader().setVisible(False)
        self._cost_table.setWordWrap(True)
        self._cost_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._cost_table.setVisible(False)

        layout = QVBoxLayout()
        layout.addStretch(1)
        layout.addWidget(self._title)
        layout.addWidget(self._hint)
        layout.addWidget(self._cost_table)
        layout.addStretch(1)
        self.setLayout(layout)

    def initializePage(self) -> None:
        w = self.wizard()
        if isinstance(w, QWizard):
            w.setButtonText(QWizard.WizardButton.FinishButton, "返回开始页")
            w.setButtonText(QWizard.WizardButton.CancelButton, "返回开始页")
        count = self._state.db_import_count
        self._title.setText("导入数据库成功")
        lines = [f"本次已写入当前总库 {count} 道题。"]
        rows = list(getattr(self._state, "import_cost_rows", []) or [])
        if bool(getattr(self._state, "import_show_costs", True)):
            total_text = str(getattr(self._state, "import_cost_total_text", "") or "").strip()
            summary = str(getattr(self._state, "import_cost_summary_text", "") or "").strip()
            detail = str(getattr(self._state, "import_cost_detail_text", "") or "").strip()
            if total_text:
                lines.append(f"本次费用总和：{total_text}")
            if summary:
                lines.append(f"本次 docx 解析费用：{summary}")
            elif detail:
                lines.append(f"本次 docx 解析费用：{detail}")
        lines.append("Markdown 导出请在开始界面的菜单栏中操作。")
        self._hint.setText("\n".join(lines))
        self._cost_table.setVisible(bool(self._state.import_show_costs and rows))
        if self._state.import_show_costs and rows:
            self._cost_table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                values = [
                    str(row.get("provider") or ""),
                    str(row.get("models") or ""),
                    str(row.get("before_balance") or ""),
                    str(row.get("current_balance") or ""),
                    str(row.get("cost") or ""),
                ]
                for col_index, value in enumerate(values):
                    item = self._cost_table.item(row_index, col_index)
                    if item is None:
                        item = QTableWidgetItem()
                        self._cost_table.setItem(row_index, col_index, item)
                    item.setText(value)
                    alignment = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
                    if col_index == 1:
                        alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    elif col_index in {2, 3, 4}:
                        alignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    item.setTextAlignment(int(alignment))
            self._cost_table.resizeRowsToContents()
        else:
            self._cost_table.setRowCount(0)

    def cleanupPage(self) -> None:
        w = self.wizard()
        if isinstance(w, QWizard):
            w.setButtonText(QWizard.WizardButton.FinishButton, "返回开始页")
            w.setButtonText(QWizard.WizardButton.CancelButton, "返回开始页")

    def nextId(self) -> int:
        return -1
