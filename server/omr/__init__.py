"""MCQ-OMR: generate randomised multiple-choice exams with machine-readable
answer sheets, and read photographed sheets with OpenCV."""

try:                                   # written by the installer build from the release tag
    from ._version import __version__  # noqa: F401
except ImportError:
    __version__ = "1.1.0"              # running from the source code
