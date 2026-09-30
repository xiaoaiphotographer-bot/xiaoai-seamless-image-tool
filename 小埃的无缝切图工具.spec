# Official PyInstaller build; only the GUI and image components are shipped.
from pathlib import Path

base = Path(SPECPATH)
a = Analysis(
    [str(base / 'main.py')],
    pathex=[str(base)],
    binaries=[],
    datas=[(str(base / 'assets'), 'assets')],
    hiddenimports=['xiaoai_slicer.verification', 'PIL.AvifImagePlugin', 'PIL.Jpeg2KImagePlugin'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'numpy', 'requests', 'pip_audit', 'PySide6.QtNetwork',
              'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtWebEngineCore',
              'PySide6.QtWebEngineWidgets', 'PySide6.QtSvg', 'PySide6.QtOpenGL',
              'PySide6.QtPdf', 'PySide6.QtPrintSupport', 'PIL.ImageTk',
              'PIL.EpsImagePlugin', 'PIL.PdfImagePlugin', 'PIL.WmfImagePlugin',
              'PIL.ImageFont', 'PIL._imagingft', 'defusedxml.xmlrpc'],
    noarchive=False,
)
# QImage displays memory pixels; no external Qt image-format plugins are needed.
# Avoid pulling in optional plugins (and their native SVG/PDF/network dependencies).
a.binaries = [entry for entry in a.binaries
              if '/imageformats/' not in entry[0].replace('\\', '/')
              and not Path(entry[0]).name.lower().startswith(('qt6svg', 'qt6pdf', 'qt6network'))
              and Path(entry[0]).name.lower() not in {
                  'icuuc.dll', 'icudt78.dll', 'qsvgicon.dll', 'qtuiotouchplugin.dll',
                  'opengl32sw.dll',
              }]
# Qt uses Windows 10's system ICU. Picking an unrelated ICU DLL from another
# application's PATH breaks imports and unnecessarily redistributes that library.
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='小埃的无缝切图工具', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
    icon=str(base / 'assets' / '小埃的无缝切图工具.ico'),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='小埃的无缝切图工具')
