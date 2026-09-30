"""Read installed Windows image codecs through WIC, without bundled HEVC libraries.

Only local file reading is requested. COM objects are released on every exit path.
The vtable slots follow the Microsoft Windows SDK wincodec.h interfaces.
"""
from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import os
from pathlib import Path
import uuid

from PIL import Image


class GUID(ct.Structure):
    _fields_ = [("Data1", ct.c_uint32), ("Data2", ct.c_uint16),
                ("Data3", ct.c_uint16), ("Data4", ct.c_ubyte * 8)]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _method(pointer, slot, result, *args):
    table = ct.cast(pointer, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
    return ct.WINFUNCTYPE(result, ct.c_void_p, *args)(table[slot])


def _check(code: int) -> None:
    if code < 0:
        raise OSError(f"Windows 图片解码失败 (0x{code & 0xffffffff:08X})")


def load_wic(path: Path, max_pixels: int) -> Image.Image:
    if os.name != "nt":
        raise OSError("Windows WIC is only available on Windows")
    ole = ct.WinDLL("ole32.dll", use_last_error=True)
    ole.CoInitializeEx.argtypes = [ct.c_void_p, wt.DWORD]
    ole.CoInitializeEx.restype = ct.c_long
    ole.CoCreateInstance.argtypes = [ct.POINTER(GUID), ct.c_void_p, wt.DWORD,
                                    ct.POINTER(GUID), ct.POINTER(ct.c_void_p)]
    ole.CoCreateInstance.restype = ct.c_long
    initialized = ole.CoInitializeEx(None, 2)
    if initialized < 0 and initialized != -2147417850:  # RPC_E_CHANGED_MODE
        _check(initialized)
    factory, decoder, frame, converter = (ct.c_void_p() for _ in range(4))
    try:
        clsid = GUID.parse("cacaf262-9370-4615-a13b-9f5539da4c0a")
        iid = GUID.parse("ec5ec8a9-c395-4314-9c77-54d7a935ff70")
        _check(ole.CoCreateInstance(ct.byref(clsid), None, 1, ct.byref(iid), ct.byref(factory)))
        _check(_method(factory, 3, ct.c_long, wt.LPCWSTR, ct.c_void_p, wt.DWORD,
                       ct.c_uint, ct.POINTER(ct.c_void_p))(
            factory, str(path), None, 0x80000000, 0, ct.byref(decoder)))
        _check(_method(decoder, 13, ct.c_long, ct.c_uint, ct.POINTER(ct.c_void_p))(
            decoder, 0, ct.byref(frame)))
        width, height = ct.c_uint(), ct.c_uint()
        _check(_method(frame, 3, ct.c_long, ct.POINTER(ct.c_uint), ct.POINTER(ct.c_uint))(
            frame, ct.byref(width), ct.byref(height)))
        if width.value < 1 or height.value < 1 or width.value * height.value > max_pixels:
            raise ValueError("图片像素数过大或尺寸无效。")
        _check(_method(factory, 10, ct.c_long, ct.POINTER(ct.c_void_p))(
            factory, ct.byref(converter)))
        pixel_format = GUID.parse("f5c7ad2d-6a8d-43dd-a7a8-a29935261ae9")  # 32bppRGBA
        _check(_method(converter, 8, ct.c_long, ct.c_void_p, ct.POINTER(GUID),
                       ct.c_uint, ct.c_void_p, ct.c_double, ct.c_uint)(
            converter, frame, ct.byref(pixel_format), 0, None, 0.0, 0))
        stride = width.value * 4
        length = stride * height.value
        buffer = (ct.c_ubyte * length)()
        _check(_method(converter, 7, ct.c_long, ct.c_void_p, ct.c_uint,
                       ct.c_uint, ct.c_void_p)(converter, None, stride, length, buffer))
        image = Image.frombytes("RGBA", (width.value, height.value), bytes(buffer))
        # WIC HEIF codecs apply container rotation. For EXIF-oriented WIC fallback
        # formats, use metadata discovered safely by Pillow when available.
        try:
            with Image.open(path, formats=("JPEG", "PNG", "TIFF")) as metadata:
                orientation = metadata.getexif().get(274, 1)
            transpose = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                         4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                         6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                         8: Image.Transpose.ROTATE_90}.get(orientation)
            if transpose:
                image = image.transpose(transpose)
        except (OSError, ValueError):
            pass
        return image
    finally:
        for pointer in (converter, frame, decoder, factory):
            if pointer.value:
                _method(pointer, 2, wt.ULONG)(pointer)
        if initialized >= 0:
            ole.CoUninitialize()
