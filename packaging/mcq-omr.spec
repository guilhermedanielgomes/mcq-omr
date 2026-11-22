# PyInstaller build of the MCQ-OMR desktop app (Windows, macOS, Linux).
#   pip install -r packaging/requirements-build.txt
#   pyinstaller packaging/mcq-omr.spec --noconfirm
# Output: dist/MCQ-OMR/ (Windows, Linux) or dist/MCQ-OMR.app (macOS).
# The installers are made from that by packaging/windows, packaging/macos and packaging/linux.
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SERVER = os.path.join(ROOT, "server")
ICONS = os.path.join(SPECPATH, "icons")
VERSION = os.environ.get("MCQ_VERSION", "1.0.0").lstrip("v")

# omr/assets: everything except header.png, a local institution logo that is never published
# (each installation sets its own header image in Settings)
ASSETS = os.path.join(SERVER, "omr", "assets")
datas = [
    (os.path.join(SERVER, "templates"), "templates"),
    (os.path.join(SERVER, "static"), "static"),
    (os.path.join(SERVER, "omr", "locales"), os.path.join("omr", "locales")),
] + [(os.path.join(ASSETS, f), os.path.join("omr", "assets"))
     for f in sorted(os.listdir(ASSETS)) if f != "header.png" and os.path.isfile(os.path.join(ASSETS, f))]

a = Analysis(
    [os.path.join(SERVER, "desktop.py")],
    pathex=[SERVER],
    datas=datas,
    hiddenimports=["app"] + collect_submodules("uvicorn"),
    excludes=["pymupdf", "fitz", "pytest", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MCQ-OMR",
    console=False,                       # no terminal window; the launcher shows its own small window
    icon=os.path.join(ICONS, "icon.icns" if sys.platform == "darwin" else "icon.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MCQ-OMR", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="MCQ-OMR.app",
        icon=os.path.join(ICONS, "icon.icns"),
        bundle_identifier="org.mcq-omr.desktop",
        version=VERSION,
        info_plist={
            "CFBundleName": "MCQ-OMR",
            "CFBundleDisplayName": "MCQ-OMR",
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "© 2026 Guilherme Daniel Gomes Silva (GGSDev). MIT licence.",
            "NSLocalNetworkUsageDescription": "MCQ-OMR receives the photos of answer sheets from the phone app on your network.",
        },
    )
