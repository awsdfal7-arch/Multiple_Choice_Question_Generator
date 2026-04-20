from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QEvent, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget


class _NoZoomWebEngineView(QWebEngineView):
    def event(self, e):
        if e.type() == QEvent.Type.Wheel:
            if QApplication.keyboardModifiers() == Qt.KeyboardModifier.ControlModifier:
                return True
        return super().event(e)


@dataclass(frozen=True)
class DocumentCopyFragment:
    page_index: int
    copied_text: str
    rects: tuple[tuple[float, float, float, float], ...]


@dataclass(frozen=True)
class DocumentCopyPayload:
    pdf_path: str
    copied_text: str
    fragments: tuple[DocumentCopyFragment, ...]

    @property
    def page_index(self) -> int:
        if not self.fragments:
            return 0
        return self.fragments[0].page_index


def _normalize_rects(values: list[Any] | tuple[Any, ...]) -> tuple[tuple[float, float, float, float], ...]:
    if not isinstance(values, (list, tuple)):
        return tuple()
    rects: list[tuple[float, float, float, float]] = []
    for raw_rect in values:
        if not isinstance(raw_rect, (list, tuple)) or len(raw_rect) != 4:
            continue
        try:
            x0, y0, x1, y1 = (float(item) for item in raw_rect)
        except (TypeError, ValueError):
            continue
        rects.append((x0, y0, x1, y1))
    return tuple(rects)


def _payload_from_mapping(mapping: dict[str, Any]) -> DocumentCopyPayload:
    fragments: list[DocumentCopyFragment] = []
    raw_fragments = mapping.get("fragments", [])
    if isinstance(raw_fragments, (list, tuple)):
        for item in raw_fragments:
            if not isinstance(item, dict):
                continue
            raw_page_index = item.get("page_index", 0)
            try:
                page_index = int(raw_page_index)
            except (TypeError, ValueError):
                try:
                    page_index = int(float(raw_page_index))
                except (TypeError, ValueError):
                    page_index = 0
            fragments.append(
                DocumentCopyFragment(
                    page_index=page_index,
                    copied_text=str(item.get("copied_text", "")),
                    rects=_normalize_rects(item.get("rects", [])),
                )
            )
    return DocumentCopyPayload(
        pdf_path=str(mapping.get("pdf_path", "")),
        copied_text=str(mapping.get("copied_text", "")),
        fragments=tuple(fragments),
    )


class PdfViewerBridge(QObject):
    viewerReadySignal = pyqtSignal()
    currentPageSignal = pyqtSignal(int)
    selectionSignal = pyqtSignal(object)
    errorSignal = pyqtSignal(str)

    @pyqtSlot()
    def viewerReady(self):
        self.viewerReadySignal.emit()

    @pyqtSlot(int)
    def reportCurrentPage(self, page_index: int):
        self.currentPageSignal.emit(int(page_index))

    @pyqtSlot(str)
    def reportSelection(self, payload_json: str):
        if not payload_json.strip():
            self.selectionSignal.emit(None)
            return
        try:
            raw_payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            self.errorSignal.emit(f"选择数据解析失败：{exc}")
            return
        if not isinstance(raw_payload, dict):
            self.selectionSignal.emit(None)
            return
        try:
            payload = _payload_from_mapping(raw_payload)
        except Exception as exc:
            self.errorSignal.emit(f"选择数据处理失败：{exc}")
            self.selectionSignal.emit(None)
            return
        self.selectionSignal.emit(payload)

    @pyqtSlot(str)
    def reportError(self, message: str):
        self.errorSignal.emit(str(message))


class DocumentPdfWebView(QWidget):
    currentPageChanged = pyqtSignal(int)
    viewerReadyChanged = pyqtSignal(bool)
    selectionChanged = pyqtSignal(object)
    errorOccurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._document_source_path: Path | None = None
        self._current_page = 0
        self._crop_enabled = False
        self._selection_outline_visible = True
        self._viewer_ready = False
        self._pending_scripts: list[str] = []
        self._current_selection_payload: DocumentCopyPayload | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.web_view = _NoZoomWebEngineView(self)
        layout.addWidget(self.web_view)

        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)

        self.web_view.page().profile().settings().setAttribute(
            QWebEngineSettings.WebAttribute.ShowScrollBars, False
        )
        self.web_view.setZoomFactor(1.0)

        self._bridge = PdfViewerBridge(self)
        self._channel = QWebChannel(self.web_view.page())
        self._channel.registerObject("bridge", self._bridge)
        self.web_view.page().setWebChannel(self._channel)

        self._bridge.viewerReadySignal.connect(self._on_viewer_ready)
        self._bridge.currentPageSignal.connect(self._on_current_page_reported)
        self._bridge.selectionSignal.connect(self._on_selection_reported)
        self._bridge.errorSignal.connect(self.errorOccurred)
        self.web_view.loadFinished.connect(self._on_view_loaded)

        self._load_frontend()

    def _frontend_path(self) -> Path:
        return Path(__file__).resolve().parent / "web" / "index.html"

    def _load_frontend(self):
        frontend_path = self._frontend_path()
        if not frontend_path.exists():
            self.errorOccurred.emit(f"前端文件不存在：{frontend_path}")
            self.web_view.setHtml(
                "<html><body style='font-family: sans-serif; padding: 12px;'>"
                "<h3>前端查看器资源缺失</h3>"
                f"<div>找不到：{frontend_path}</div>"
                "</body></html>"
            )
            return
        self.web_view.load(QUrl.fromLocalFile(str(frontend_path)))

    def _invoke_js(self, script: str):
        if not self._viewer_ready:
            self._pending_scripts.append(script)
            return
        self.web_view.page().runJavaScript(script)

    def _call_js(self, function_name: str, *args: Any):
        serialized_args = ", ".join(json.dumps(arg) for arg in args)
        script = f"window.viewerApi && window.viewerApi.{function_name}({serialized_args});"
        self._invoke_js(script)

    def _flush_pending_scripts(self):
        pending = list(self._pending_scripts)
        self._pending_scripts.clear()
        for script in pending:
            self.web_view.page().runJavaScript(script)

    def _on_view_loaded(self, ok: bool):
        if not ok:
            self.errorOccurred.emit("前端查看器加载失败")

    def _on_viewer_ready(self):
        self._viewer_ready = True
        self.viewerReadyChanged.emit(True)
        self._flush_pending_scripts()
        self._call_js("setCropEnabled", self._crop_enabled)
        self._call_js("setSelectionOutlineVisible", self._selection_outline_visible)

    def _on_current_page_reported(self, page_index: int):
        page_index = int(page_index)
        if self._current_page == page_index:
            return
        self._current_page = page_index
        self.currentPageChanged.emit(page_index)

    def _on_selection_reported(self, payload: object | None):
        self._current_selection_payload = payload if isinstance(payload, DocumentCopyPayload) else None
        self.selectionChanged.emit(self._current_selection_payload)

    def open_pdf(self, pdf_path: Path):
        resolved_path = pdf_path.resolve()
        if not resolved_path.exists():
            self.errorOccurred.emit(f"PDF 文件不存在：{resolved_path}")
            return
        self._document_source_path = resolved_path
        self._current_selection_payload = None
        url = QUrl.fromLocalFile(str(resolved_path))
        try:
            pdf_url = url.toString(QUrl.UrlFormattingOption.FullyEncoded)
        except Exception:
            pdf_url = bytes(url.toEncoded()).decode("utf-8", errors="ignore")
        self._call_js("loadPdf", pdf_url)

    def setCropEnabled(self, enabled: bool):
        self._crop_enabled = bool(enabled)
        self._call_js("setCropEnabled", self._crop_enabled)

    def setSelectionOutlineVisible(self, visible: bool):
        self._selection_outline_visible = bool(visible)
        self._call_js("setSelectionOutlineVisible", self._selection_outline_visible)

    def clear_selection_visual(self):
        self._current_selection_payload = None
        self._call_js("clearSelectionVisual")

    def jumpToPage(self, page_index: int):
        self._call_js("jumpToPage", int(page_index))

    def currentPage(self) -> int:
        return self._current_page

    def current_selection_payload(self) -> DocumentCopyPayload | None:
        return self._current_selection_payload
