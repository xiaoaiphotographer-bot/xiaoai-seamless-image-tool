"""Image loading, composition and integer-aligned slicing, without UI dependencies."""
from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import tempfile
import warnings
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

MAX_INPUT_PIXELS = 80_000_000
MAX_OUTPUT_PIXELS = 72_000_000
MAX_FILE_BYTES = 512 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
SRGB_PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
# Never invoke document/vector plugins, including EPS/Ghostscript or WMF helpers.
# Restrict by decoder, not merely extension: renaming a script to .jpg changes nothing.
DECODER_FORMATS = ("JPEG", "PNG", "WEBP", "TIFF", "BMP", "GIF", "AVIF",
                   "ICO", "TGA", "PPM", "PSD", "JPEG2000")
IMAGE_EXTENSIONS = (
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".tif", ".tiff",
    ".bmp", ".gif", ".avif", ".ico", ".tga", ".ppm", ".pgm", ".pbm",
    ".psd", ".jp2", ".j2k", ".jpf", ".heic", ".heif", ".hif", ".jxr", ".wdp",
)


class ImageError(ValueError):
    """An image operation that can be explained directly to the user."""


@dataclass(frozen=True)
class Layout:
    platform: str = "instagram"
    columns: int = 3
    rows: int = 1
    tile_width: int = 1080
    tile_height: int = 1350

    @property
    def size(self) -> tuple[int, int]:
        return self.columns * self.tile_width, self.rows * self.tile_height

    @property
    def count(self) -> int:
        return self.columns * self.rows

    @property
    def name(self) -> str:
        if self.platform == "instagram":
            return f"Instagram · {self.count} 张连续滑动"
        return f"微信 · {self.count} 宫格 ({self.columns} 列 × {self.rows} 行)"

    def validate(self) -> None:
        if self.platform == "instagram":
            if self.rows != 1 or not 2 <= self.columns <= 20:
                raise ImageError("Instagram 切图数量应为 2 至 20 张。")
        elif self.platform == "wechat":
            if (self.columns, self.rows) not in ((2, 2), (3, 2), (2, 3), (3, 3)):
                raise ImageError("请选择四格、横向六格、竖向六格或九格。")
            if self.tile_width != self.tile_height:
                raise ImageError("微信宫格的每张图片必须为正方形。")
        else:
            raise ImageError("未知发布平台。")
        if not 256 <= self.tile_width <= 2160 or not 144 <= self.tile_height <= 3840:
            raise ImageError("请选择有效的导出尺寸。")
        if math.prod(self.size) > MAX_OUTPUT_PIXELS:
            raise ImageError("当前总尺寸过大，请减少张数或调低每张图片的尺寸。")


@dataclass
class Transform:
    angle: float = 0.0  # Clockwise, identical to Qt's screen-coordinate rotation.
    zoom: float = 1.0   # Multiple of the cover scale at the current angle.
    pan_x: float = 0.0  # Fractions of the complete output canvas.
    pan_y: float = 0.0
    fill: bool = True
    background: str = "white"


@dataclass
class LoadedImage:
    image: Image.Image
    path: Path
    format: str
    notes: list[str]


def load_image(path: str | Path) -> LoadedImage:
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ImageError("请选择一个图片文件。")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ImageError("这张图片超过 512 MB，请先缩小文件。")
    notes: list[str] = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path, formats=DECODER_FORMATS) as source:
                if source.width * source.height > MAX_INPUT_PIXELS:
                    raise ImageError("这张图片超过 8000 万像素，请先缩小尺寸。")
                fmt = source.format or path.suffix.lstrip(".").upper()
                if getattr(source, "n_frames", 1) > 1:
                    notes.append("动画或多页图片：使用第一帧 / 第一页。")
                source.seek(0)
                source.load()
                oriented = ImageOps.exif_transpose(source)
                profile = oriented.info.get("icc_profile")
                alpha = oriented.convert("RGBA").getchannel("A")
                if profile:
                    try:
                        input_profile = ImageCms.ImageCmsProfile(io.BytesIO(profile))
                        color = oriented if oriented.mode in ("RGB", "CMYK", "LAB") else oriented.convert("RGB")
                        converted = ImageCms.profileToProfile(
                            color, input_profile, ImageCms.ImageCmsProfile(io.BytesIO(SRGB_PROFILE)),
                            outputMode="RGB",
                        )
                        image = converted.convert("RGBA")
                        image.putalpha(alpha)
                    except (ImageCms.PyCMSError, OSError, ValueError):
                        image = oriented.convert("RGBA")
                        notes.append("原图色彩配置无法解析，已使用默认色彩转换。")
                else:
                    image = oriented.convert("RGBA")
                image.info.clear()  # Do not propagate GPS or other original metadata.
    except UnidentifiedImageError as exc:
        if os.name == "nt" and path.suffix.lower() in (".heic", ".heif", ".hif", ".jxr", ".wdp"):
            from .wic import load_wic
            try:
                image = load_wic(path, MAX_INPUT_PIXELS)
                fmt = path.suffix.lstrip(".").upper()
                notes.append("通过 Windows 系统图片解码器读取。")
            except (OSError, ValueError) as wic_error:
                raise ImageError(
                    "系统无法解码这张 HEIC / HEIF 图片。请在 Microsoft Store 安装“HEIF 图像扩展”"
                    "和所需的 HEVC 解码扩展，或先转换为 JPG / PNG。"
                ) from wic_error
        else:
            raise ImageError("无法读取这张图片。请检查文件是否损坏，或转换为 JPG / PNG。") from exc
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageError("图片像素数过大，已停止读取。请先缩小图片。") from exc
    except (OSError, SyntaxError) as exc:
        raise ImageError(f"图片读取失败：{exc}") from exc
    return LoadedImage(image, path, fmt, notes)


def cover_scale(image_size: tuple[int, int], canvas_size: tuple[int, int], angle: float) -> float:
    radians = math.radians(angle)
    cosine, sine = abs(math.cos(radians)), abs(math.sin(radians))
    width, height = canvas_size
    return max((cosine * width + sine * height) / image_size[0],
               (sine * width + cosine * height) / image_size[1])


def normalized_transform(image_size: tuple[int, int], layout: Layout, state: Transform) -> Transform:
    state = replace(state)
    if not all(math.isfinite(n) for n in (state.angle, state.zoom, state.pan_x, state.pan_y)):
        raise ImageError("图片位置或角度无效，请点击重置。")
    state.angle = (state.angle + 180.0) % 360.0 - 180.0
    state.zoom = max(1.0 if state.fill else 0.001, min(6.0, state.zoom))
    if state.background not in ("white", "black", "transparent"):
        state.background = "white"
    if state.fill:
        width, height = layout.size
        rad = math.radians(state.angle)
        c, s = math.cos(rad), math.sin(rad)
        scale = cover_scale(image_size, layout.size, state.angle) * state.zoom
        # Clamp translation in the original image's local axes. This guarantees
        # that every corner of the crop is covered even after arbitrary rotation.
        limit_x = max(0.0, (scale * image_size[0] - (abs(c) * width + abs(s) * height)) / 2)
        limit_y = max(0.0, (scale * image_size[1] - (abs(s) * width + abs(c) * height)) / 2)
        px, py = state.pan_x * width, state.pan_y * height
        local_x = max(-limit_x, min(limit_x, c * px + s * py))
        local_y = max(-limit_y, min(limit_y, -s * px + c * py))
        state.pan_x = (c * local_x - s * local_y) / width
        state.pan_y = (s * local_x + c * local_y) / height
    return state


def render_master(image: Image.Image, layout: Layout, state: Transform,
                  output_size: tuple[int, int] | None = None) -> Image.Image:
    """Render once before slicing, so no tile has a separately rounded transform."""
    layout.validate()
    state = normalized_transform(image.size, layout, state)
    target = output_size or layout.size
    if min(target) < 1 or math.prod(target) > MAX_OUTPUT_PIXELS:
        raise ImageError("输出尺寸无效。")
    width, height = layout.size
    factor_x, factor_y = target[0] / width, target[1] / height
    scale = cover_scale(image.size, layout.size, state.angle) * state.zoom
    rad = math.radians(state.angle)
    c, s = math.cos(rad) / scale, math.sin(rad) / scale
    tx, ty = width * (0.5 + state.pan_x), height * (0.5 + state.pan_y)
    inverse = (c / factor_x, s / factor_y, image.width / 2 - c * tx - s * ty,
               -s / factor_x, c / factor_y, image.height / 2 + s * tx - c * ty)
    # Pillow performs RGBA affine resampling with premultiplied alpha internally.
    warped = image.transform(target, Image.Transform.AFFINE, inverse,
                             resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0, 0))
    if state.background == "transparent":
        return warped
    background = (255, 255, 255, 255) if state.background == "white" else (0, 0, 0, 255)
    return Image.alpha_composite(Image.new("RGBA", target, background), warped)


def slice_boxes(layout: Layout) -> list[tuple[int, int, int, int]]:
    return [(column * layout.tile_width, row * layout.tile_height,
             (column + 1) * layout.tile_width, (row + 1) * layout.tile_height)
            for row in range(layout.rows) for column in range(layout.columns)]


def safe_stem(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:64]
    if not cleaned or cleaned.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        cleaned = "image"
    return cleaned


def export_images(loaded: LoadedImage, layout: Layout, state: Transform,
                  directory: str | Path, output_format: str = "JPEG", quality: int = 95,
                  save_overview: bool = True, progress: Callable[[int, str], None] | None = None,
                  cancelled: Callable[[], bool] | None = None) -> Path:
    """Write only a new, uniquely named job folder, committing after all saves succeed."""
    layout.validate()
    if output_format not in ("JPEG", "PNG", "WEBP"):
        raise ImageError("请选择 JPG、PNG 或 WebP 导出。")
    state = normalized_transform(loaded.image.size, layout, state)
    if output_format == "JPEG" and state.background == "transparent":
        state.background = "white"
    root = Path(directory).resolve(strict=True)
    if not root.is_dir():
        raise ImageError("请选择一个存在的导出文件夹。")
    stage = Path(tempfile.mkdtemp(prefix=".xiaoai_slicer-", dir=root))
    try:
        if cancelled and cancelled():
            raise InterruptedError("导出已取消。")
        if progress:
            progress(5, "正在生成完整画面…")
        master = render_master(loaded.image, layout, state)
        extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[output_format]
        file_names: list[str] = []
        for number, box in enumerate(slice_boxes(layout), 1):
            if cancelled and cancelled():
                raise InterruptedError("导出已取消。")
            file_name = f"{number:02d}.{extension}"
            tile = master.crop(box)
            options: dict = {"icc_profile": SRGB_PROFILE}
            if output_format == "JPEG":
                tile = tile.convert("RGB")
                options.update(quality=max(50, min(100, quality)), subsampling=0, optimize=True)
            elif output_format == "WEBP":
                options.update(quality=max(50, min(100, quality)), method=6)
            else:
                options.update(compress_level=6)
            tile.save(stage / file_name, output_format, **options)
            file_names.append(file_name)
            if progress:
                progress(10 + round(80 * number / layout.count), f"正在保存 {number} / {layout.count}…")
        # Extra reference files are isolated from the numbered publishing images.
        reference = stage / "发布参考"
        reference.mkdir()
        if save_overview:
            preview = master.copy()
            preview.thumbnail((2400, 1600), Image.Resampling.LANCZOS)
            preview.save(reference / "完整画面.png", "PNG", icc_profile=SRGB_PROFILE)
        manifest = {
            "application": "小埃的无缝切图工具 1.0.0", "platform": layout.platform,
            "columns": layout.columns, "rows": layout.rows,
            "tile_size": [layout.tile_width, layout.tile_height], "files": file_names,
            "order": "left-to-right, then top-to-bottom", "transform": vars(state),
            "format": output_format, "metadata": "Original EXIF and GPS are not exported",
        }
        (reference / "切图信息.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        order = "从左到右依次上传" if layout.platform == "instagram" else "从左到右、从上到下依次上传"
        (reference / "发布顺序.txt").write_text(
            f"{layout.name}\n每张 {layout.tile_width} × {layout.tile_height} 像素\n\n"
            f"{order}：\n" + "\n".join(file_names) +
            "\n\n只选择上一级文件夹中的编号图片。平台展示间距和重新压缩由平台决定。\n"
            "本软件的切图边界没有重叠、缺失或人为间隙。\n", encoding="utf-8")
        if cancelled and cancelled():
            raise InterruptedError("导出已取消。")
        stem = f"{safe_stem(loaded.path.stem)}_{layout.platform}_{layout.count}图_{datetime.now():%Y%m%d_%H%M%S}"
        for attempt in range(1000):
            result = root / (stem if attempt == 0 else f"{stem}_{attempt + 1}")
            try:
                # On Windows rename never overwrites an existing directory.
                if result.exists():
                    continue
                stage.rename(result)
                if progress:
                    progress(100, "导出完成")
                return result
            except FileExistsError:
                continue
        raise ImageError("导出文件夹重名过多，请选择另一个目录。")
    finally:
        if stage.exists() and stage.resolve().parent == root and stage.name.startswith(".xiaoai_slicer-"):
            shutil.rmtree(stage)
