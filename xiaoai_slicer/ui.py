"""Native desktop UI with a live crop canvas and asynchronous local exports."""
from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path

from PIL import Image
from PySide6.QtCore import (
    Qt, QRectF, QPointF, QObject, QRunnable, QThreadPool, Signal, QTimer, QUrl,
)
from PySide6.QtGui import (
    QColor, QPainter, QPen, QFont, QImage, QDesktopServices, QKeySequence,
    QShortcut, QIcon, QPixmap, QPalette,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QHBoxLayout,
    QVBoxLayout, QComboBox, QSpinBox, QDoubleSpinBox, QSlider, QCheckBox,
    QFileDialog, QMessageBox, QScrollArea, QStackedWidget, QProgressBar, QDialog,
    QLineEdit,
)

from .core import (
    Layout, Transform, LoadedImage, load_image, render_master, normalized_transform,
    cover_scale, export_images, IMAGE_EXTENSIONS,
)

ACCENT = QColor("#ff9559")
TEXT = QColor("#edf0f3")
MUTED = QColor("#89929e")

STYLE = """
QMainWindow, QDialog { background: #0f1216; }
QWidget { color: #edf0f3; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px; }
QFrame#panel { background: #181d24; border: 1px solid #2a313b; border-radius: 14px; }
QLabel { background: transparent; }
QLabel#muted { color: #89929e; }
QLabel#section { color: #a9b2bf; font-size: 11px; font-weight: 600; }
QPushButton { background: #252c36; border: 1px solid #333d4b; border-radius: 8px; padding: 9px 12px; font-weight: 500; }
QPushButton:hover { background: #303946; border-color: #596576; }
QPushButton:pressed { background: #394454; }
QPushButton:disabled { color: #646d79; background: #20262f; border-color: #2a313b; }
QPushButton#primary { background: #ff9559; border: 1px solid #ff9559; color: #201610; font-weight: 700; }
QPushButton#primary:hover { background: #ffad7c; }
QPushButton#primary:disabled { background: #4c3930; border-color: #4c3930; color: #927763; }
QPushButton#mode { padding: 10px 6px; }
QPushButton#mode:checked { background: #3c2c24; color: #ffac79; border-color: #d27d4d; }
QPushButton#small { padding: 5px 9px; font-size: 11px; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: #11161d; border: 1px solid #343e4c; border-radius: 7px;
    padding: 6px 9px; min-height: 21px; selection-background-color: #ae633d;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: #ff9559; }
QComboBox QAbstractItemView { background: #232b36; color: #edf0f3; selection-background-color: #68432e; }
QComboBox::drop-down { border: none; width: 25px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { width: 18px; border-left: 1px solid #343e4c; }
QSpinBox::down-button, QDoubleSpinBox::down-button { width: 18px; border-left: 1px solid #343e4c; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #4b5868; border-radius: 4px; background: #11161d; }
QCheckBox::indicator:checked { background: #ff9559; border-color: #ff9559; image: none; }
QSlider::groove:horizontal { height: 4px; background: #323c49; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #cf7b4d; border-radius: 2px; }
QSlider::handle:horizontal { width: 13px; margin: -5px 0; border-radius: 6px; background: #ffad79; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 7px; background: transparent; margin: 0; }
QScrollBar::handle:vertical { background: #3b4654; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QProgressBar { background: #222a35; border: none; border-radius: 3px; height: 6px; text-align: center; }
QProgressBar::chunk { background: #ff9559; border-radius: 3px; }
QToolTip { background: #303b49; color: #edf0f3; border: 1px solid #4d596a; padding: 6px; }
"""
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets"
STYLE += f"""
QComboBox::down-arrow {{ image: url('{(ASSETS / 'down.png').as_posix()}'); width: 10px; height: 10px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url('{(ASSETS / 'up.png').as_posix()}'); width: 9px; height: 9px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url('{(ASSETS / 'down.png').as_posix()}'); width: 9px; height: 9px; }}
QCheckBox::indicator:checked {{ image: url('{(ASSETS / 'check.png').as_posix()}'); }}
"""


def label(text: str, muted: bool = False, size: int | None = None) -> QLabel:
    item = QLabel(text)
    if muted:
        item.setObjectName("muted")
    if size:
        item.setStyleSheet(f"font-size: {size}px;")
    return item


def button(text: str, handler, kind: str | None = None) -> QPushButton:
    item = QPushButton(text)
    if kind:
        item.setObjectName(kind)
    item.clicked.connect(handler)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    return item


def to_qimage(image: Image.Image) -> QImage:
    rgba = image.convert("RGBA")
    return QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4,
                  QImage.Format.Format_RGBA8888).copy()


def app_icon() -> QIcon:
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ff9559"))
    p.drawRoundedRect(QRectF(5, 5, 118, 118), 28, 28)
    p.setBrush(QColor("#241c19"))
    for row in range(2):
        for col in range(3):
            p.drawRoundedRect(QRectF(24 + col * 28, 36 + row * 30, 23, 25), 4, 4)
    p.end()
    return QIcon(pixmap)


class CropCanvas(QWidget):
    changed = Signal(bool)  # True marks the end of a gesture for undo history.
    file_dropped = Signal(str)
    upload_requested = Signal()

    def __init__(self):
        super().__init__()
        self.loaded: LoadedImage | None = None
        self.preview: QImage | None = None
        self.layout = Layout()
        self.state = Transform()
        self.locked = False
        self.drag_over = False
        self.drag_mode: str | None = None
        self.start_position = QPointF()
        self.start_state = Transform()
        self.start_angle = 0.0
        self.setMinimumSize(360, 230)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.upload_button = button("＋ 选择图片", self.upload_requested.emit, "primary")
        self.upload_button.setParent(self)
        self.upload_button.setFixedSize(146, 40)

    def set_image(self, loaded: LoadedImage):
        self.loaded = loaded
        small = loaded.image.copy()
        small.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
        self.preview = to_qimage(small)
        self.upload_button.hide()
        self.state = Transform()
        self.update()

    def crop_rect(self) -> QRectF:
        width, height = self.layout.size
        scale = min(max(50, self.width() - 96) / width,
                    max(50, self.height() - 112) / height)
        return QRectF((self.width() - width * scale) / 2,
                      (self.height() - height * scale) / 2 + 8,
                      width * scale, height * scale)

    def rotation_handle(self) -> QPointF:
        rect = self.crop_rect()
        return QPointF(rect.center().x(), rect.top() - 26)

    def resizeEvent(self, event):
        self.upload_button.move((self.width() - self.upload_button.width()) // 2,
                                self.height() // 2 + 30)
        super().resizeEvent(event)

    def normalize(self):
        if self.loaded:
            self.state = normalized_transform(self.loaded.image.size, self.layout, self.state)

    def _draw_image(self, p: QPainter, rect: QRectF):
        if not self.preview or not self.loaded:
            return
        view_scale = rect.width() / self.layout.size[0]
        image_scale = cover_scale(self.loaded.image.size, self.layout.size, self.state.angle) * self.state.zoom
        p.save()
        p.translate(rect.center().x() + self.state.pan_x * rect.width(),
                    rect.center().y() + self.state.pan_y * rect.height())
        p.rotate(self.state.angle)
        p.scale(image_scale * view_scale * self.loaded.image.width / self.preview.width(),
                image_scale * view_scale * self.loaded.image.height / self.preview.height())
        p.drawImage(QPointF(-self.preview.width() / 2, -self.preview.height() / 2), self.preview)
        p.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setPen(QPen(QColor("#2b3440"), 1))
        p.setBrush(QColor("#14191f"))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 14, 14)
        p.setPen(QPen(QColor("#242c36"), 1))
        for x in range(22, self.width(), 24):
            for y in range(22, self.height(), 24):
                p.drawPoint(x, y)
        if not self.loaded:
            area = QRectF(35, 30, self.width() - 70, self.height() - 60)
            pen = QPen(ACCENT if self.drag_over else QColor("#414b5b"), 1.4, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(QColor("#1b222b") if self.drag_over else Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(area, 12, 12)
            center = QPointF(self.width() / 2, self.height() / 2 - 80)
            p.setPen(QPen(ACCENT, 2))
            p.setBrush(QColor("#30251f"))
            p.drawRoundedRect(QRectF(center.x() - 37, center.y() - 26, 74, 52), 8, 8)
            p.drawLine(QPointF(center.x() - 12, center.y() - 26), QPointF(center.x() - 12, center.y() + 26))
            p.drawLine(QPointF(center.x() + 12, center.y() - 26), QPointF(center.x() + 12, center.y() + 26))
            p.setPen(TEXT)
            p.setFont(QFont("Microsoft YaHei UI", 16, QFont.Weight.DemiBold))
            p.drawText(QRectF(10, self.height() / 2 - 38, self.width() - 20, 35), Qt.AlignmentFlag.AlignCenter, "让一张照片，连成一个故事")
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.setPen(MUTED)
            p.drawText(QRectF(10, self.height() / 2 + 1, self.width() - 20, 24), Qt.AlignmentFlag.AlignCenter, "拖入图片，或点击下方按钮导入")
            p.drawText(QRectF(10, self.height() / 2 + 84, self.width() - 20, 45), Qt.AlignmentFlag.AlignCenter, "JPG · PNG · WebP · TIFF · BMP · GIF · AVIF 等\nHEIC 使用 Windows 已安装的解码扩展")
            p.end()
            return
        rect = self.crop_rect()
        p.save()
        p.setClipRect(QRectF(12, 12, self.width() - 24, self.height() - 24))
        p.setOpacity(0.15)
        self._draw_image(p, rect)
        p.restore()
        p.save()
        p.setClipRect(rect)
        if self.state.background == "transparent":
            cell = 12
            for row, y in enumerate(range(int(rect.top()), int(rect.bottom()) + cell, cell)):
                for col, x in enumerate(range(int(rect.left()), int(rect.right()) + cell, cell)):
                    p.fillRect(x, y, cell, cell, QColor("#dedede") if (row + col) % 2 else QColor("#ffffff"))
        else:
            p.fillRect(rect, QColor("white" if self.state.background == "white" else "black"))
        self._draw_image(p, rect)
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#f5f5f5"), 1.4))
        p.drawRect(rect)
        p.setPen(QPen(QColor(255, 255, 255, 175), 1, Qt.PenStyle.DashLine))
        for col in range(1, self.layout.columns):
            x = rect.left() + col * rect.width() / self.layout.columns
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for row in range(1, self.layout.rows):
            y = rect.top() + row * rect.height() / self.layout.rows
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        tile_width, tile_height = rect.width() / self.layout.columns, rect.height() / self.layout.rows
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        if tile_width >= 22 and tile_height >= 28:
            for number in range(self.layout.count):
                row, col = divmod(number, self.layout.columns)
                badge = QRectF(rect.left() + col * tile_width + 5, rect.top() + row * tile_height + 5, 23, 20)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(10, 14, 20, 185))
                p.drawRoundedRect(badge, 5, 5)
                p.setPen(TEXT)
                p.drawText(badge, Qt.AlignmentFlag.AlignCenter, f"{number + 1:02d}")
        handle = self.rotation_handle()
        p.setPen(QPen(ACCENT, 1.3))
        p.drawLine(QPointF(handle.x(), handle.y() + 10), QPointF(handle.x(), rect.top()))
        p.setBrush(QColor("#30251f"))
        p.drawEllipse(handle, 11, 11)
        p.drawArc(QRectF(handle.x() - 5, handle.y() - 5, 10, 10), 20 * 16, 280 * 16)
        p.setPen(MUTED)
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.drawText(QRectF(handle.x() + 18, handle.y() - 11, 150, 22), Qt.AlignmentFlag.AlignVCenter, f"旋转 {self.state.angle:+.1f}°")
        p.drawText(QRectF(14, self.height() - 30, self.width() - 28, 20), Qt.AlignmentFlag.AlignCenter, "拖动移动  ·  滚轮缩放  ·  拖动上方圆点旋转  ·  Alt + 拖动旋转")
        if self.drag_over:
            p.setPen(QPen(ACCENT, 2))
            p.setBrush(QColor(255, 149, 89, 20))
            p.drawRoundedRect(QRectF(3, 3, self.width() - 6, self.height() - 6), 13, 13)
        p.end()

    def mousePressEvent(self, event):
        if self.locked or not self.loaded:
            return
        point = event.position()
        handle = self.rotation_handle()
        rotating = math.hypot(point.x() - handle.x(), point.y() - handle.y()) <= 18
        rotating = rotating or event.button() == Qt.MouseButton.RightButton or bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return
        self.drag_mode = "rotate" if rotating else "pan"
        self.start_position = point
        self.start_state = replace(self.state)
        center = self.crop_rect().center()
        self.start_angle = math.degrees(math.atan2(point.y() - center.y(), point.x() - center.x()))
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        point = event.position()
        if self.drag_mode and self.loaded and not self.locked:
            rect = self.crop_rect()
            if self.drag_mode == "pan":
                self.state.pan_x = self.start_state.pan_x + (point.x() - self.start_position.x()) / rect.width()
                self.state.pan_y = self.start_state.pan_y + (point.y() - self.start_position.y()) / rect.height()
            else:
                center = rect.center()
                angle = math.degrees(math.atan2(point.y() - center.y(), point.x() - center.x()))
                self.state.angle = self.start_state.angle + angle - self.start_angle
            self.normalize()
            self.changed.emit(False)
            self.update()
        elif self.loaded:
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseReleaseEvent(self, event):
        if self.drag_mode:
            self.drag_mode = None
            self.changed.emit(True)
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, event):
        if not self.locked and self.loaded:
            self.state.pan_x = self.state.pan_y = 0
            self.changed.emit(True)
            self.update()

    def wheelEvent(self, event):
        if not self.loaded or self.locked:
            return
        self.zoom_at(self.state.zoom * (1.12 ** (event.angleDelta().y() / 120)), event.position())
        self.changed.emit(True)
        event.accept()

    def zoom_at(self, zoom: float, point: QPointF | None = None):
        if not self.loaded:
            return
        old = self.state.zoom
        zoom = max(1 if self.state.fill else 0.001, min(6, zoom))
        rect = self.crop_rect()
        if point is not None and old > 0:
            qx, qy = (point.x() - rect.center().x()) / rect.width(), (point.y() - rect.center().y()) / rect.height()
            self.state.pan_x = qx + (self.state.pan_x - qx) * zoom / old
            self.state.pan_y = qy + (self.state.pan_y - qy) * zoom / old
        self.state.zoom = zoom
        self.normalize()
        self.update()

    def dragEnterEvent(self, event):
        if not self.locked and event.mimeData().hasUrls():
            files = [url for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).is_file()]
            if len(files) == 1:
                event.acceptProposedAction()
                self.drag_over = True
                self.update()

    def dragLeaveEvent(self, event):
        self.drag_over = False
        self.update()

    def dropEvent(self, event):
        self.drag_over = False
        self.update()
        if not self.locked:
            files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).is_file()]
            if len(files) == 1:
                self.file_dropped.emit(files[0])
                event.acceptProposedAction()


class TilePreview(QWidget):
    """A horizontal contact strip; no guides or numbers are baked into export pixels."""
    def __init__(self):
        super().__init__()
        self.layout_spec = Layout()
        self.tiles: list[QImage] = []
        self.setFixedHeight(112)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet("background: #181d24;")

    def set_tiles(self, image: Image.Image, layout: Layout):
        self.layout_spec = layout
        self.tiles = []
        for row in range(layout.rows):
            for col in range(layout.columns):
                box = (round(col * image.width / layout.columns), round(row * image.height / layout.rows),
                       round((col + 1) * image.width / layout.columns), round((row + 1) * image.height / layout.rows))
                self.tiles.append(to_qimage(image.crop(box)))
        width = max(54, round(78 * layout.tile_width / layout.tile_height))
        self.setMinimumWidth(layout.count * (width + 10) + 10)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.tiles:
            p.setPen(MUTED)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "导入图片后，在这里预览每张切图")
            return
        width = max(54, round(78 * self.layout_spec.tile_width / self.layout_spec.tile_height))
        for index, image in enumerate(self.tiles):
            rect = QRectF(5 + index * (width + 10), 4, width, 78)
            p.fillRect(rect, QColor("#303844"))
            p.drawImage(rect, image)
            p.setPen(MUTED)
            p.drawText(QRectF(rect.left(), 87, width, 20), Qt.AlignmentFlag.AlignCenter, f"{index + 1:02d}")
        p.end()


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.result.emit(self.operation(self.signals.progress.emit))
        except Exception as exc:
            self.signals.error.emit(str(exc) or type(exc).__name__)
        finally:
            self.signals.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("小埃的无缝切图工具 · 无缝切图")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(940, 620)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1260, screen.width() - 36), min(870, max(620, screen.height() - 55)))
        self.loaded: LoadedImage | None = None
        self.last_directory = str(Path.home() / "Pictures")
        self.last_export: Path | None = None
        self.busy = False
        self.cancel_requested = False
        self.close_after_job = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.workers: set[Worker] = set()
        self.history: list[tuple[Layout, Transform]] = []
        self.history_index = -1
        self.syncing = False
        self._build()
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(120)
        self.preview_timer.timeout.connect(self.refresh_tiles)
        for keys, action in (("Ctrl+O", self.choose_image), ("Ctrl+E", self.choose_export),
                             ("Ctrl+0", self.reset), ("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(action)
        self.apply_layout()

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(18)
        header = QHBoxLayout()
        mark = label("▦")
        mark.setStyleSheet("font-size: 35px; color: #ff9559;")
        header.addWidget(mark)
        title = QVBoxLayout()
        title.setSpacing(1)
        brand = label("小埃的无缝切图工具", size=21)
        brand.setStyleSheet("font-family: 'Microsoft YaHei'; font-size: 21px; font-weight: 700;")
        title.addWidget(brand)
        title.addWidget(label("无缝切图 / 一张照片，无限延续", muted=True, size=11))
        header.addLayout(title)
        header.addStretch()
        local = label("●  本机处理", muted=True, size=11)
        header.addWidget(local)
        header.addSpacing(12)
        header.addWidget(button("关于", self.show_about, "small"))
        self.import_button = button("＋ 导入图片", self.choose_image)
        header.addWidget(self.import_button)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(20)
        side_container = QWidget()
        side_container.setFixedWidth(296)
        side_column = QVBoxLayout(side_container)
        side_column.setContentsMargins(0, 0, 0, 0)
        side_column.setSpacing(12)
        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setFixedWidth(296)
        self.settings = QFrame()
        self.settings.setObjectName("panel")
        side = QVBoxLayout(self.settings)
        side.setContentsMargins(18, 20, 18, 20)
        side.setSpacing(10)
        side_scroll.setWidget(self.settings)
        side_column.addWidget(side_scroll, 1)
        body.addWidget(side_container)
        self._section(side, "01 / 发布方式")
        modes = QHBoxLayout()
        modes.setSpacing(8)
        self.ins_button = button("Instagram", lambda: self.change_platform("instagram"), "mode")
        self.wechat_button = button("微信朋友圈", lambda: self.change_platform("wechat"), "mode")
        for item in (self.ins_button, self.wechat_button):
            item.setCheckable(True)
            modes.addWidget(item)
        self.ins_button.setChecked(True)
        side.addLayout(modes)
        self.platform_options = QStackedWidget()
        instagram = QWidget()
        ins_options = QVBoxLayout(instagram)
        ins_options.setContentsMargins(0, 0, 0, 0)
        ins_options.setSpacing(10)
        self.count = QSpinBox()
        self.count.setRange(2, 20)
        self.count.setValue(3)
        self.count.setSuffix(" 张")
        self._row(ins_options, "连续切图", self.count)
        self.ratio = QComboBox()
        for text, data in (("4 : 5  经典竖图", (4, 5)), ("1 : 1  正方形", (1, 1)),
                           ("3 : 4  竖图", (3, 4)), ("16 : 9  横图", (16, 9))):
            self.ratio.addItem(text, data)
        ins_options.addWidget(self.ratio)
        self.platform_options.addWidget(instagram)
        wechat = QWidget()
        wechat_options = QVBoxLayout(wechat)
        wechat_options.setContentsMargins(0, 0, 0, 0)
        self.grid = QComboBox()
        for text, data in (("四宫格 · 2 列 × 2 行", (2, 2)), ("六宫格 · 3 列 × 2 行", (3, 2)),
                           ("六宫格 · 2 列 × 3 行", (2, 3)), ("九宫格 · 3 列 × 3 行", (3, 3))):
            self.grid.addItem(text, data)
        self.grid.setCurrentIndex(3)
        wechat_options.addWidget(self.grid)
        wechat_options.addWidget(label("每张为正方形，按阅读顺序编号", True, 11))
        self.platform_options.addWidget(wechat)
        side.addWidget(self.platform_options)
        self._divider(side)
        self._section(side, "02 / 构图调整")
        self.transform_controls = QWidget()
        controls = QVBoxLayout(self.transform_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(10)
        self.zoom = QDoubleSpinBox()
        self.zoom.setRange(0.1, 600)
        self.zoom.setDecimals(1)
        self.zoom.setValue(100)
        self.zoom.setSuffix(" %")
        self._row(controls, "图片缩放", self.zoom)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(1, 600)
        self.zoom_slider.setValue(100)
        controls.addWidget(self.zoom_slider)
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-180, 180)
        self.angle.setDecimals(1)
        self.angle.setSingleStep(0.5)
        self.angle.setSuffix(" °")
        self._row(controls, "旋转角度", self.angle)
        self.angle_slider = QSlider(Qt.Orientation.Horizontal)
        self.angle_slider.setRange(-1800, 1800)
        controls.addWidget(self.angle_slider)
        rotate_buttons = QHBoxLayout()
        rotate_buttons.addWidget(button("↶ 90°", lambda: self.rotate(-90), "small"))
        rotate_buttons.addWidget(button("↷ 90°", lambda: self.rotate(90), "small"))
        rotate_buttons.addWidget(button("重置", self.reset, "small"))
        controls.addLayout(rotate_buttons)
        self.fill = QCheckBox("自动铺满画框")
        self.fill.setChecked(True)
        self.fill.setToolTip("防止旋转和移动时露出空白；关闭后可以自由缩小图片。")
        controls.addWidget(self.fill)
        self.background = QComboBox()
        for text, value in (("留白底色 · 白色", "white"), ("留白底色 · 黑色", "black"), ("透明背景 · PNG / WebP", "transparent")):
            self.background.addItem(text, value)
        controls.addWidget(self.background)
        fitting = QHBoxLayout()
        fitting.addWidget(button("铺满", self.fit_cover, "small"))
        fitting.addWidget(button("完整显示", self.fit_contain, "small"))
        controls.addLayout(fitting)
        self.transform_controls.setEnabled(False)
        side.addWidget(self.transform_controls)
        self._divider(side)
        self._section(side, "03 / 导出选项")
        self.resolution = QComboBox()
        for value in (1080, 1440, 2160, 720):
            self.resolution.addItem(f"{value} px / 每张宽度", value)
        side.addWidget(self.resolution)
        self.format = QComboBox()
        for text, data in (("JPG · 适合直接发布", "JPEG"), ("PNG · 无损 / 支持透明", "PNG"), ("WebP · 文件更小", "WEBP")):
            self.format.addItem(text, data)
        side.addWidget(self.format)
        self.quality = QSpinBox()
        self.quality.setRange(50, 100)
        self.quality.setValue(95)
        self._row(side, "导出质量", self.quality)
        self.overview = QCheckBox("附带完整画面和发布顺序")
        self.overview.setChecked(True)
        side.addWidget(self.overview)
        self.size_info = label("", True, 11)
        self.size_info.setWordWrap(True)
        side.addWidget(self.size_info)
        side.addStretch()
        self.export_button = button("选择目录并导出  →", self.choose_export, "primary")
        self.export_button.setMinimumHeight(44)
        self.export_button.setEnabled(False)
        side_column.addWidget(self.export_button)

        preview_column = QVBoxLayout()
        preview_column.setSpacing(12)
        top = QHBoxLayout()
        self.layout_title = label("Instagram · 3 张连续滑动", size=15)
        top.addWidget(self.layout_title)
        top.addStretch()
        self.undo_button = button("↶ 撤销", self.undo, "small")
        self.redo_button = button("↷ 重做", self.redo, "small")
        top.addWidget(self.undo_button)
        top.addWidget(self.redo_button)
        preview_column.addLayout(top)
        self.canvas = CropCanvas()
        self.canvas.file_dropped.connect(self.start_load)
        self.canvas.upload_requested.connect(self.choose_image)
        self.canvas.changed.connect(self.transform_changed)
        preview_column.addWidget(self.canvas, 1)
        self.file_info = label("还没有导入图片", True, 11)
        self.file_info.setTextFormat(Qt.TextFormat.PlainText)
        preview_column.addWidget(self.file_info)
        tile_heading = QHBoxLayout()
        tile_heading.addWidget(label("切图预览", size=13))
        tile_heading.addStretch()
        self.order_info = label("从左到右依次发布", True, 11)
        tile_heading.addWidget(self.order_info)
        preview_column.addLayout(tile_heading)
        self.tile_strip = TilePreview()
        self.tile_scroll = QScrollArea()
        self.tile_scroll.setWidget(self.tile_strip)
        self.tile_scroll.setWidgetResizable(True)
        self.tile_scroll.setFixedHeight(126)
        preview_column.addWidget(self.tile_scroll)
        body.addLayout(preview_column, 1)
        outer.addLayout(body, 1)
        bottom = QHBoxLayout()
        self.status = label("准备就绪 · Ctrl+O 导入 / Ctrl+E 导出", True, 11)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        bottom.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(140)
        self.progress.setTextVisible(False)
        self.progress.hide()
        bottom.addWidget(self.progress)
        self.cancel_button = button("取消导出", self.cancel_export, "small")
        self.cancel_button.hide()
        bottom.addWidget(self.cancel_button)
        outer.addLayout(bottom)

        self.count.valueChanged.connect(self.apply_layout)
        self.ratio.currentIndexChanged.connect(self.apply_layout)
        self.grid.currentIndexChanged.connect(self.apply_layout)
        self.resolution.currentIndexChanged.connect(self.apply_layout)
        self.zoom.valueChanged.connect(self.zoom_changed)
        self.zoom.editingFinished.connect(self.record_history)
        self.zoom_slider.valueChanged.connect(self.zoom_changed)
        self.zoom_slider.sliderReleased.connect(self.record_history)
        self.angle.valueChanged.connect(self.angle_changed)
        self.angle.editingFinished.connect(self.record_history)
        self.angle_slider.valueChanged.connect(lambda value: self.angle_changed(value / 10))
        self.angle_slider.sliderReleased.connect(self.record_history)
        self.fill.toggled.connect(self.fill_changed)
        self.background.currentIndexChanged.connect(self.background_changed)
        self.format.currentIndexChanged.connect(self.format_changed)

    @staticmethod
    def _row(parent, text, widget):
        row = QHBoxLayout()
        row.addWidget(label(text))
        row.addStretch()
        widget.setFixedWidth(112)
        row.addWidget(widget)
        parent.addLayout(row)

    @staticmethod
    def _section(parent, text):
        item = label(text)
        item.setObjectName("section")
        parent.addWidget(item)

    @staticmethod
    def _divider(parent):
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #2c3440;")
        parent.addSpacing(3)
        parent.addWidget(line)
        parent.addSpacing(3)

    def change_platform(self, platform: str):
        if self.busy:
            return
        self.ins_button.setChecked(platform == "instagram")
        self.wechat_button.setChecked(platform == "wechat")
        self.platform_options.setCurrentIndex(0 if platform == "instagram" else 1)
        self.apply_layout()

    def current_layout(self) -> Layout:
        width = self.resolution.currentData()
        if self.ins_button.isChecked():
            x, y = self.ratio.currentData()
            return Layout("instagram", self.count.value(), 1, width, round(width * y / x))
        columns, rows = self.grid.currentData()
        return Layout("wechat", columns, rows, width, width)

    def apply_layout(self, *_):
        if self.syncing:
            return
        layout = self.current_layout()
        self.canvas.layout = layout
        self.canvas.normalize()
        self.layout_title.setText(layout.name)
        self.order_info.setText("从左到右依次发布" if layout.platform == "instagram" else "从左到右、从上到下依次发布")
        self.size_info.setText(f"共 {layout.count} 张 · 每张 {layout.tile_width} × {layout.tile_height}\n完整画面 {layout.size[0]} × {layout.size[1]} px")
        self.canvas.update()
        self.transform_changed(True)

    def transform_changed(self, final: bool = False):
        self.sync_controls()
        self.preview_timer.start()
        if final:
            self.record_history()

    def sync_controls(self):
        self.syncing = True
        state = self.canvas.state
        self.zoom.setMinimum(100 if state.fill else 0.1)
        self.zoom_slider.setMinimum(100 if state.fill else 1)
        self.zoom.setValue(state.zoom * 100)
        self.zoom_slider.setValue(round(state.zoom * 100))
        self.angle.setValue(state.angle)
        self.angle_slider.setValue(round(state.angle * 10))
        self.fill.setChecked(state.fill)
        self.background.setCurrentIndex(self.background.findData(state.background))
        self.quality.setEnabled(self.format.currentData() != "PNG")
        self.syncing = False
        self.undo_button.setEnabled(not self.busy and self.history_index > 0)
        self.redo_button.setEnabled(not self.busy and self.history_index < len(self.history) - 1)

    def zoom_changed(self, value):
        if self.syncing or not self.loaded or self.busy:
            return
        self.canvas.zoom_at(float(value) / 100)
        self.transform_changed(False)

    def angle_changed(self, value):
        if self.syncing or not self.loaded or self.busy:
            return
        self.canvas.state.angle = float(value)
        self.canvas.normalize()
        self.canvas.update()
        self.transform_changed(False)

    def fill_changed(self, checked):
        if self.syncing or not self.loaded or self.busy:
            return
        self.canvas.state.fill = checked
        self.canvas.normalize()
        self.canvas.update()
        self.transform_changed(True)

    def background_changed(self, *_):
        if self.syncing or self.busy:
            return
        self.canvas.state.background = self.background.currentData()
        if self.canvas.state.background == "transparent" and self.format.currentData() == "JPEG":
            self.format.setCurrentIndex(self.format.findData("PNG"))
        self.canvas.update()
        self.transform_changed(True)

    def format_changed(self, *_):
        if self.format.currentData() == "JPEG" and self.canvas.state.background == "transparent":
            self.canvas.state.background = "white"
            self.status.setText("JPG 使用白色背景；需要透明时请选择 PNG 或 WebP。")
            self.canvas.update()
            self.transform_changed(True)
        self.sync_controls()

    def rotate(self, amount: float):
        if not self.loaded or self.busy:
            return
        self.canvas.state.angle += amount
        self.canvas.normalize()
        self.canvas.update()
        self.transform_changed(True)

    def reset(self):
        if not self.loaded or self.busy:
            return
        self.canvas.state = Transform()
        self.canvas.update()
        self.transform_changed(True)

    def fit_cover(self):
        if not self.loaded or self.busy:
            return
        self.canvas.state.fill = True
        self.canvas.state.zoom = 1
        self.canvas.state.pan_x = self.canvas.state.pan_y = 0
        self.canvas.normalize()
        self.canvas.update()
        self.transform_changed(True)

    def fit_contain(self):
        if not self.loaded or self.busy:
            return
        state = self.canvas.state
        width, height = self.canvas.layout.size
        rad = math.radians(state.angle)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        fit = min(width / (c * self.loaded.image.width + s * self.loaded.image.height),
                  height / (s * self.loaded.image.width + c * self.loaded.image.height))
        state.fill = False
        state.zoom = max(0.001, fit / cover_scale(self.loaded.image.size, (width, height), state.angle))
        state.pan_x = state.pan_y = 0
        self.canvas.update()
        self.transform_changed(True)

    def record_history(self):
        if not self.loaded or self.syncing:
            return
        snapshot = (self.canvas.layout, replace(self.canvas.state))
        if 0 <= self.history_index < len(self.history) and self.history[self.history_index] == snapshot:
            return
        self.history = self.history[:self.history_index + 1]
        self.history.append(snapshot)
        self.history = self.history[-60:]
        self.history_index = len(self.history) - 1
        self.sync_controls()

    def restore_history(self):
        layout, state = self.history[self.history_index]
        self.syncing = True
        self.ins_button.setChecked(layout.platform == "instagram")
        self.wechat_button.setChecked(layout.platform == "wechat")
        self.platform_options.setCurrentIndex(0 if layout.platform == "instagram" else 1)
        self.resolution.setCurrentIndex(self.resolution.findData(layout.tile_width))
        if layout.platform == "instagram":
            self.count.setValue(layout.columns)
            ratio = next((i for i in range(self.ratio.count()) if round(layout.tile_width * self.ratio.itemData(i)[1] / self.ratio.itemData(i)[0]) == layout.tile_height), 0)
            self.ratio.setCurrentIndex(ratio)
        else:
            self.grid.setCurrentIndex(self.grid.findData((layout.columns, layout.rows)))
        self.canvas.layout, self.canvas.state = layout, replace(state)
        self.syncing = False
        self.layout_title.setText(layout.name)
        self.order_info.setText("从左到右依次发布" if layout.platform == "instagram" else "从左到右、从上到下依次发布")
        self.size_info.setText(f"共 {layout.count} 张 · 每张 {layout.tile_width} × {layout.tile_height}\n完整画面 {layout.size[0]} × {layout.size[1]} px")
        self.canvas.update()
        self.transform_changed(False)

    def undo(self):
        if not self.busy and self.history_index > 0:
            self.history_index -= 1
            self.restore_history()

    def redo(self):
        if not self.busy and self.history_index < len(self.history) - 1:
            self.history_index += 1
            self.restore_history()

    def refresh_tiles(self):
        if not self.loaded or self.busy:
            return
        layout = self.canvas.layout
        try:
            # Thumbnail processing occurs only after gestures settle; canvas moves live.
            layout.validate()
            factor = min(1400 / layout.size[0], 600 / layout.size[1], 1)
            size = (max(layout.columns, round(layout.size[0] * factor)),
                    max(layout.rows, round(layout.size[1] * factor)))
            image = render_master(self.loaded.image, layout, self.canvas.state, size)
            self.tile_strip.set_tiles(image, layout)
        except ValueError as exc:
            self.status.setText(str(exc))

    def choose_image(self):
        if self.busy:
            return
        extensions = " ".join("*" + ext for ext in IMAGE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(self, "导入一张图片", self.last_directory,
                                              f"常见图片 ({extensions});;所有文件 (*)")
        if path:
            self.start_load(path)

    def set_busy(self, busy: bool, exporting: bool = False):
        self.busy = busy
        self.settings.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.canvas.locked = busy
        self.canvas.upload_button.setEnabled(not busy)
        self.export_button.setEnabled(not busy and self.loaded is not None)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy and exporting)
        self.sync_controls()
        if not busy:
            self.preview_timer.start()

    def run_worker(self, operation, result):
        worker = Worker(operation)
        self.workers.add(worker)
        worker.signals.result.connect(result)
        worker.signals.error.connect(self.job_error)
        worker.signals.progress.connect(self.job_progress)
        worker.signals.finished.connect(lambda: self.job_finished(worker))
        self.pool.start(worker)

    def start_load(self, path: str):
        if self.busy:
            return
        self.progress.setRange(0, 0)
        self.status.setText("正在读取图片…")
        self.set_busy(True)
        self.run_worker(lambda progress: load_image(path), self.image_loaded)

    def image_loaded(self, loaded: LoadedImage):
        self.loaded = loaded
        self.canvas.set_image(loaded)
        self.last_directory = str(loaded.path.parent)
        self.file_info.setText(f"{loaded.path.name}  ·  {loaded.image.width:,} × {loaded.image.height:,}  ·  {loaded.format}")
        self.transform_controls.setEnabled(True)
        self.history = []
        self.history_index = -1
        self.record_history()
        self.status.setText("  ".join(loaded.notes) if loaded.notes else "图片已导入 · 拖动构图后，选择目录导出")

    def choose_export(self):
        if not self.loaded or self.busy:
            return
        try:
            self.canvas.layout.validate()
        except ValueError as exc:
            QMessageBox.information(self, "请调整导出尺寸", str(exc))
            return
        # This is the operating system directory picker, called for EVERY export.
        directory = QFileDialog.getExistingDirectory(self, "选择切图导出目录", self.last_directory,
                                                      QFileDialog.Option.ShowDirsOnly)
        if not directory:
            return
        self.start_export(directory)

    def start_export(self, directory: str):
        if not self.loaded or self.busy:
            return
        layout, state, loaded = self.canvas.layout, replace(self.canvas.state), self.loaded
        fmt, quality, overview = self.format.currentData(), self.quality.value(), self.overview.isChecked()
        self.cancel_requested = False
        self.last_directory = directory
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.set_busy(True, exporting=True)
        self.status.setText("正在准备切图…")
        self.run_worker(lambda progress: export_images(loaded, layout, state, directory,
                        fmt, quality, overview, progress, lambda: self.cancel_requested), self.export_done)

    def export_done(self, path: Path):
        self.last_export = path
        self.status.setText(f"已导出 {self.canvas.layout.count} 张切图 · {path.name}")
        if self.close_after_job:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("切图导出完成")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(16)
        title = label(f"✓  {self.canvas.layout.count} 张切图已准备好", size=20)
        title.setStyleSheet("color: #ffad79; font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        info = label("按照 01、02、03… 的编号顺序上传即可。\n每次导出会创建新文件夹，原图保持不变。", True)
        layout.addWidget(info)
        location = QLineEdit(str(path))
        location.setReadOnly(True)
        layout.addWidget(location)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("继续编辑", dialog.accept))
        actions.addWidget(button("打开导出文件夹", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))), "primary"))
        layout.addLayout(actions)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setModal(True)
        dialog.show()

    def cancel_export(self):
        self.cancel_requested = True
        self.cancel_button.setEnabled(False)
        self.status.setText("正在取消，当前图片处理完成后停止…")

    def job_progress(self, value: int, message: str):
        self.progress.setValue(value)
        self.status.setText(message)

    def job_error(self, message: str):
        self.status.setText(message)
        if not self.cancel_requested and not self.close_after_job:
            QMessageBox.warning(self, "操作未完成", message)

    def job_finished(self, worker: Worker):
        self.workers.discard(worker)
        self.set_busy(False)
        self.cancel_button.setEnabled(True)
        if self.close_after_job:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        if self.busy:
            self.close_after_job = True
            self.cancel_requested = True
            self.status.setText("正在结束当前操作…")
            event.ignore()
        else:
            event.accept()

    def show_about(self):
        box = QMessageBox(self)
        box.setWindowTitle("关于 小埃的无缝切图工具")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText("小埃的无缝切图工具 1.0.0 · 无缝切图\n\n"
                    "照片在本机处理。无需账号，不上传照片。\n"
                    "程序源码：MIT 许可证；界面使用 Qt / PySide6 (LGPLv3)。\n"
                    "图像处理使用 Pillow (MIT-CMU)。依赖版权、许可证全文及对应源码见软件目录。\n\n"
                    "GIF / 多页 TIFF 使用第一帧 / 第一页。PSD 读取合成图。\n"
                    "HEIC / HEIF 依赖 Windows HEIF / HEVC 系统解码扩展。\n"
                    "切图像素连续；发布平台可能添加显示间距或重新压缩。")
        box.exec()


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("小埃的无缝切图工具")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("小埃的无缝切图工具")
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, "#0f1216"),
                        (QPalette.ColorRole.WindowText, "#edf0f3"),
                        (QPalette.ColorRole.Base, "#11161d"),
                        (QPalette.ColorRole.AlternateBase, "#181d24"),
                        (QPalette.ColorRole.Text, "#edf0f3"),
                        (QPalette.ColorRole.Button, "#252c36"),
                        (QPalette.ColorRole.ButtonText, "#edf0f3"),
                        (QPalette.ColorRole.Highlight, "#ae633d")):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1 and sys.argv[1] == "--verify" and len(sys.argv) == 3:
        def verify():
            try:
                from .verification import verify_packaged
                verify_packaged(app, window, Path(sys.argv[2]))
            except Exception:
                import json
                import traceback
                (Path(sys.argv[2]) / "verification.json").write_text(
                    json.dumps({"passed": False, "error": traceback.format_exc()}, ensure_ascii=False, indent=2), encoding="utf-8")
                app.exit(1)
        QTimer.singleShot(250, verify)
    elif len(sys.argv) == 2 and Path(sys.argv[1]).is_file():
        QTimer.singleShot(0, lambda: window.start_load(sys.argv[1]))
    return app.exec()
