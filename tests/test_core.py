"""Behavioral checks: real format decoding, geometry, exact reconstruction and safe writes."""
import hashlib
import math
import random
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from xiaoai_slicer.core import (
    Layout, Transform, LoadedImage, ImageError, load_image, render_master,
    normalized_transform, export_images, slice_boxes, safe_stem,
)


def fixture(size=(1800, 900)):
    image = Image.new("RGBA", size, (28, 57, 76, 255))
    draw = ImageDraw.Draw(image)
    for x in range(0, size[0], 29):
        draw.rectangle((x, 0, x + 14, size[1]), fill=(x % 231 + 12, 80, 210, 255))
    for y in range(0, size[1], 37):
        draw.line((0, y, size[0], y), fill=(220, y % 190 + 30, 64, 255), width=3)
    draw.ellipse((size[0] * .4, size[1] * .2, size[0] * .7, size[1] * .8), fill=(242, 146, 64, 255))
    return image


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.image = fixture()
        self.source = self.root / "测试照片.png"
        self.image.save(self.source)
        self.loaded = load_image(self.source)

    def tearDown(self):
        self.directory.cleanup()

    def test_png_exports_reassemble_exactly_for_all_layouts_and_arbitrary_rotation(self):
        layouts = [Layout("instagram", count, 1, 256, height) for count in (2, 3, 7, 20) for height in (256, 320)]
        layouts += [Layout("wechat", x, y, 256, 256) for x, y in ((2, 2), (3, 2), (2, 3), (3, 3))]
        for layout in layouts:
            with self.subTest(layout=layout):
                state = Transform(31.7, 1.31, .04, -.12)
                reference = render_master(self.image, layout, state)
                output = export_images(self.loaded, layout, state, self.root, "PNG")
                reassembled = Image.new("RGBA", layout.size)
                for index, box in enumerate(slice_boxes(layout), 1):
                    with Image.open(output / f"{index:02d}.png") as tile:
                        self.assertEqual(tile.size, (layout.tile_width, layout.tile_height))
                        reassembled.paste(tile.convert("RGBA"), box[:2])
                self.assertEqual(reassembled.tobytes(), reference.tobytes())

    def test_cover_never_exposes_background_after_rotate_and_pan(self):
        rng = random.Random(22)
        source = Image.new("RGBA", (317, 541), (30, 130, 220, 255))
        for _ in range(35):
            state = Transform(rng.uniform(-180, 180), rng.uniform(.2, 4), rng.uniform(-3, 3), rng.uniform(-3, 3), True, "transparent")
            layout = Layout("wechat", 3, 2, 256, 256)
            master = render_master(source, layout, state)
            self.assertEqual(master.getchannel("A").getextrema(), (255, 255))

    def test_identity_geometry_has_no_subpixel_translation(self):
        source = fixture((768, 256))
        output = render_master(source, Layout("instagram", 3, 1, 256, 256), Transform())
        self.assertEqual(source.tobytes(), output.tobytes())

    def test_clockwise_rotation_matches_known_90_degree_orientation(self):
        source = fixture((768, 256))
        layout = Layout("wechat", 3, 3, 256, 256)
        first = render_master(source, layout, Transform(90, background="transparent"))
        second = render_master(source.transpose(Image.Transpose.ROTATE_270), layout, Transform(background="transparent"))
        self.assertLessEqual(max(ImageChops.difference(first, second).getextrema()[i][1] for i in range(4)), 1)

    def test_common_formats_are_actually_decoded(self):
        for fmt, suffix in (("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp"), ("TIFF", "tiff"),
                            ("BMP", "bmp"), ("GIF", "gif"), ("AVIF", "avif"), ("TGA", "tga"), ("PPM", "ppm"), ("JPEG2000", "jp2")):
            with self.subTest(format=fmt):
                path = self.root / f"图片.{suffix}"
                self.image.convert("RGB").save(path, fmt)
                loaded = load_image(path)
                self.assertEqual(loaded.image.size, self.image.size)
                self.assertEqual(loaded.image.mode, "RGBA")
        small = self.image.resize((128, 64))
        path = self.root / "icon.ico"
        small.save(path, "ICO", sizes=[(128, 64)])
        self.assertEqual(load_image(path).image.size, small.size)

    def test_exif_orientation_is_applied_and_metadata_is_removed(self):
        source = fixture((600, 300)).convert("RGB")
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = "Private description"
        path = self.root / "orientation.jpg"
        source.save(path, exif=exif)
        loaded = load_image(path)
        self.assertEqual(loaded.image.size, (300, 600))
        output = export_images(loaded, Layout("wechat", 2, 2, 256, 256), Transform(), self.root)
        with Image.open(output / "01.jpg") as tile:
            self.assertFalse(tile.getexif())
            self.assertTrue(tile.info["icc_profile"])

    def test_first_frame_of_animation_and_multipage_image(self):
        red = Image.new("RGB", (400, 300), "red")
        blue = Image.new("RGB", (400, 300), "blue")
        for suffix, fmt in (("gif", "GIF"), ("tiff", "TIFF")):
            path = self.root / f"animation.{suffix}"
            red.save(path, fmt, save_all=True, append_images=[blue])
            loaded = load_image(path)
            self.assertEqual(loaded.image.getpixel((20, 20)), (255, 0, 0, 255))
            self.assertTrue(loaded.notes)

    def test_transparency_and_lossy_export_compatibility(self):
        layout = Layout("wechat", 2, 2, 256, 256)
        state = Transform(zoom=.3, fill=False, background="transparent")
        for fmt, suffix in (("PNG", "png"), ("WEBP", "webp"), ("JPEG", "jpg")):
            output = export_images(self.loaded, layout, state, self.root, fmt)
            with Image.open(output / f"01.{suffix}") as tile:
                if fmt == "JPEG":
                    self.assertEqual(tile.getpixel((0, 0)), (255, 255, 255))
                else:
                    self.assertEqual(tile.convert("RGBA").getpixel((0, 0))[3], 0)

    def test_cancel_and_failure_leave_no_partial_export_or_source_changes(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        entries = set(self.root.iterdir())
        with self.assertRaises(InterruptedError):
            export_images(self.loaded, Layout(), Transform(), self.root, cancelled=lambda: True)
        self.assertEqual(set(self.root.iterdir()), entries)
        calls = []
        def progress(value, text):
            calls.append(value)
        with self.assertRaises(InterruptedError):
            export_images(self.loaded, Layout(), Transform(), self.root, progress=progress, cancelled=lambda: len(calls) >= 3)
        self.assertEqual(set(self.root.iterdir()), entries)
        self.assertEqual(before, hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_repeat_exports_never_overwrite(self):
        layout = Layout("wechat", 2, 2, 256, 256)
        first = export_images(self.loaded, layout, Transform(), self.root, "PNG")
        sentinel = first / "keep.txt"
        sentinel.write_text("keep")
        second = export_images(self.loaded, layout, Transform(), self.root, "PNG")
        self.assertNotEqual(first, second)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_rejects_corruption_oversized_output_and_invalid_numbers(self):
        invalid = self.root / "not-an-image.png"
        invalid.write_bytes(b"not a png")
        with self.assertRaises(ImageError):
            load_image(invalid)
        with self.assertRaises(ImageError):
            Layout("instagram", 20, 1, 2160, 2880).validate()
        with self.assertRaises(ImageError):
            normalized_transform(self.image.size, Layout(), Transform(angle=math.nan))

    def test_script_formats_renamed_as_photos_are_not_decoded(self):
        disguised = self.root / "script.jpg"
        disguised.write_bytes(b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 100\nshowpage\n")
        with self.assertRaises(ImageError):
            load_image(disguised)

    def test_export_name_cannot_escape_directory_or_use_windows_reserved_names(self):
        self.assertEqual(safe_stem("CON"), "image")
        self.assertNotIn("/", safe_stem("../../image"))
        self.assertNotIn("\\", safe_stem("a\\b"))


if __name__ == "__main__":
    unittest.main()
