"""Render a public link URL as a scannable QR code for physical engagements."""

from __future__ import annotations

import io

import segno


def link_qr_svg(url):
    """Encode a public link URL as a self-contained, print-friendly SVG QR code.

    Args:
        url: Absolute public link URL to encode; never visitor-controlled content.

    Returns:
        UTF-8 SVG markup using fixed black-on-white modules for reliable scanning
        regardless of the viewer's light or dark theme.
    """
    buffer = io.BytesIO()
    segno.make(url, error="m").save(
        buffer,
        kind="svg",
        xmldecl=False,
        svgns=True,
        scale=8,
        border=2,
        dark="#000000",
        light="#ffffff",
    )
    return buffer.getvalue().decode("utf-8")
