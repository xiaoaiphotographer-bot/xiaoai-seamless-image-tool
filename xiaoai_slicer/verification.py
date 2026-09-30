"""Reproducible integration verification for the source and the packaged executable.

Use 小埃的无缝切图工具.exe --verify <an existing writable directory> to run this check.
It creates its own synthetic image, never reading the user's photo library.
"""
from __future__ import annotations
import json
import traceback
from pathlib import Path
from contextlib import contextmanager

from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QWheelEvent, QImage, QPainter, QFont, QColor, QPolygon, QPen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QDialog, QApplication

from .core import Layout, Transform, render_master, load_image


@contextmanager
def picker_result(name, value):
    """Replace only our own app's Qt dialog call for automated verification."""
    original = getattr(QFileDialog, name)
    calls = []
    def replacement(*args, **kwargs):
        calls.append((args, kwargs))
        return value
    setattr(QFileDialog, name, replacement)
    try:
        yield calls
    finally:
        setattr(QFileDialog, name, original)


def make_demo(path: Path):
    """Draw an original test landscape; no external photo copyrights are involved."""
    qimage = QImage(2400, 1200, QImage.Format.Format_RGB32)
    painter = QPainter(qimage)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for y in range(1200):
        t = y / 1200
        painter.setPen(QColor(round(211 - 101 * t), round(164 - 79 * t), round(119 - 54 * t)))
        painter.drawLine(0, y, 2400, y)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#efcfa1"))
    painter.drawEllipse(1510, 75, 360, 360)
    for color, points in [
        ("#66787a", [(0, 730), (360, 200), (690, 710), (1040, 390), (1390, 800), (1890, 500), (2400, 680), (2400, 1200), (0, 1200)]),
        ("#344f57", [(0, 820), (380, 675), (720, 800), (1150, 590), (1540, 905), (1960, 670), (2400, 820), (2400, 1200), (0, 1200)]),
        ("#183e48", [(0, 920), (420, 805), (840, 965), (1210, 855), (1640, 990), (2180, 830), (2400, 895), (2400, 1200), (0, 1200)]),
    ]:
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
    painter.setPen(QPen(QColor("#c99b74"), 8))
    painter.drawPolyline(QPolygon([QPoint(x, y) for x, y in [(0, 1040), (500, 970), (1050, 1100), (1510, 1000), (2060, 1080), (2400, 1030)]]))
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = QFont("Segoe UI", 60, QFont.Weight.Bold)
    font.setPixelSize(94)
    painter.setFont(font)
    painter.setPen(QColor("#fff5e4"))
    painter.drawText(110, 164, "STAY CONNECTED")
    font.setPixelSize(26)
    font.setWeight(QFont.Weight.Normal)
    painter.setFont(font)
    painter.setPen(QColor("#f6e2c8"))
    painter.drawText(115, 231, "ONE IMAGE. EVERY FRAME.")
    painter.end()
    qimage.save(str(path), "PNG")


def wait_until(predicate, app, timeout=20000):
    for _ in range(timeout // 20):
        app.processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError("UI operation timed out")


def verify_packaged(app, window, root: Path):
    checks = []
    report = {"checks": checks, "passed": False}
    try:
        root = root.resolve(strict=True)
        demo = root / "verification-demo.png"
        make_demo(demo)
        window.grab().save(str(root / "01-empty.png"))
        # Upload button path, including the local file-picker request.
        with picker_result("getOpenFileName", (str(demo), "")) as picker:
            QTest.mouseClick(window.import_button, Qt.MouseButton.LeftButton)
            assert len(picker) == 1
        wait_until(lambda: window.loaded is not None and not window.busy, app)
        checks.append("Upload button calls file picker and loads actual PNG")
        assert window.canvas.preview is not None
        assert window.export_button.isEnabled() and window.export_button.isVisible()
        format_root = root / "format-checks"
        format_root.mkdir(exist_ok=True)
        sample = window.loaded.image.resize((160, 100)).convert("RGB")
        for fmt, suffix in (("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp"), ("TIFF", "tiff"),
                            ("BMP", "bmp"), ("GIF", "gif"), ("AVIF", "avif"), ("TGA", "tga"),
                            ("PPM", "ppm"), ("JPEG2000", "jp2")):
            path = format_root / f"sample.{suffix}"
            sample.save(path, fmt)
            assert load_image(path).image.size == sample.size
        from .wic import load_wic
        assert load_wic(format_root / "sample.png", 1000000).size == sample.size
        checks.append("Packaged codecs decode JPG/PNG/WebP/TIFF/BMP/GIF/AVIF/TGA/PPM/JP2; Windows WIC reads PNG")
        window.refresh_tiles()
        app.processEvents()
        window.grab().save(str(root / "02-instagram.png"))

        # Real Qt pointer and wheel events, sent to our own canvas.
        canvas = window.canvas
        rect = canvas.crop_rect()
        point = rect.center().toPoint()
        wheel = QWheelEvent(QPointF(point), QPointF(canvas.mapToGlobal(point)), QPoint(0, 0), QPoint(0, 360),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(canvas, wheel)
        assert canvas.state.zoom > 1.3
        before = (canvas.state.pan_x, canvas.state.pan_y)
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(canvas, point + QPoint(34, 21), delay=30)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=point + QPoint(34, 21))
        assert (canvas.state.pan_x, canvas.state.pan_y) != before
        handle = canvas.rotation_handle().toPoint()
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=handle)
        QTest.mouseMove(canvas, handle + QPoint(42, 8), delay=30)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=handle + QPoint(42, 8))
        assert abs(canvas.state.angle) > 1
        checks.append("Pointer dragging moves image; wheel zooms; rotation handle rotates")
        current = canvas.state.angle
        window.undo()
        assert canvas.state.angle != current
        window.redo()
        assert canvas.state.angle == current
        checks.append("Undo and redo restore actual crop transforms")

        # Every export must call the directory picker, even on repeat exports.
        window.format.setCurrentIndex(window.format.findData("PNG"))
        window.resolution.setCurrentIndex(window.resolution.findData(720))
        for platform, grid_index in (("instagram", None), ("wechat", 0), ("wechat", 1), ("wechat", 2), ("wechat", 3)):
            window.change_platform(platform)
            if grid_index is not None:
                window.grid.setCurrentIndex(grid_index)
            window.last_export = None
            layout = window.canvas.layout
            reference = render_master(window.loaded.image, layout, window.canvas.state)
            with picker_result("getExistingDirectory", str(root)) as picker:
                window.choose_export()
                assert len(picker) == 1
                assert not (picker[0][0][3] & QFileDialog.Option.DontUseNativeDialog)
            wait_until(lambda: window.last_export is not None and not window.busy, app)
            output = window.last_export
            files = sorted(output.glob("[0-9][0-9].png"))
            assert len(files) == layout.count
            assembled = Image.new("RGBA", layout.size)
            for number, file in enumerate(files):
                row, col = divmod(number, layout.columns)
                with Image.open(file) as tile:
                    assert tile.size == (layout.tile_width, layout.tile_height)
                    assembled.paste(tile, (col * layout.tile_width, row * layout.tile_height))
            assert assembled.tobytes() == reference.tobytes()
            for dialog in window.findChildren(QDialog):
                dialog.accept()
            checks.append(f"Native directory picker requested; {layout.name}; all PNG tiles reconstruct exactly")
        window.resolution.setCurrentIndex(window.resolution.findData(1080))
        window.reset()
        window.refresh_tiles()
        app.processEvents()
        window.grab().save(str(root / "03-wechat.png"))

        with picker_result("getExistingDirectory", ""):
            last = window.last_export
            window.choose_export()
            assert not window.busy and window.last_export == last
        checks.append("Cancelling directory picker performs no export")

        # Drag-and-drop exercises URL handling with the same real local file.
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(demo))])
        enter = QDragEnterEvent(QPoint(60, 60), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(canvas, enter)
        assert enter.isAccepted()
        drop = QDropEvent(QPointF(60, 60), Qt.DropAction.CopyAction, mime,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(canvas, drop)
        assert drop.isAccepted()
        wait_until(lambda: not window.busy, app)
        assert window.loaded.path == demo
        checks.append("Real local file drag-and-drop imports through background loader")
        window.change_platform("instagram")
        window.resolution.setCurrentIndex(window.resolution.findData(1080))
        window.reset()
        window.refresh_tiles()
        app.processEvents()
        window.grab().save(str(root / "04-final.png"))
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        (root / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window.close()
        app.exit(0 if report["passed"] else 1)
