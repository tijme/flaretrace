"""Prevent padded README banners and distorted or downsampled admin screenshot previews."""

from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ElementTree

import pytest

#: Repository assets are tested directly, not copies inside the installed Python wheel.
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "name", ["admin-links", "admin-visits", "admin-visit-inspector"]
)
def test_screenshots_remain_native_widescreen_captures(name):
    """Require original 1920×1080 browser pixels instead of stretched thumbnail files.

    Args:
        name: Screenshot basename used by one of the README’s three gallery previews.
    """
    contents = (ROOT / "assets/screenshots" / (name + ".png")).read_bytes()
    assert contents[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", contents[16:24]) == (1920, 1080)


def test_readme_previews_preserve_intrinsic_image_ratio():
    """Keep three width-only previews so browser layout cannot force the wrong image height."""
    source = (ROOT / "README.md").read_text()
    previews = re.findall(r'<img\b[^>]*src="assets/screenshots/[^>]+>', source)
    assert len(previews) == 3
    assert all(
        'width="320"' in preview and "height=" not in preview for preview in previews
    )


def test_theme_banners_share_a_tight_transparent_canvas():
    """Keep both vector variants identically cropped without introducing a background rectangle."""
    canvases = []
    for name in ("flaretrace.svg", "flaretrace-dark.svg"):
        root = ElementTree.parse(ROOT / "assets" / name).getroot()
        canvas = tuple(float(value) for value in root.attrib["viewBox"].split())
        canvases.append(canvas)
        assert canvas == (59.8, 37.1, 874.7, 113)
        assert float(root.attrib["width"]) / float(
            root.attrib["height"]
        ) == pytest.approx(canvas[2] / canvas[3])
        assert not root.findall(".//{http://www.w3.org/2000/svg}rect")
        assert all(
            path.attrib.get("mask")
            for path in root.findall("{http://www.w3.org/2000/svg}path")
        )
    assert canvases[0] == canvases[1]
