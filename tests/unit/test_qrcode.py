"""Exercise the print-friendly SVG QR code renderer for public link URLs."""

from flaretrace.links.qrcode import link_qr_svg


def test_renders_self_contained_black_on_white_svg():
    """Produce a self-contained SVG using fixed black-on-white modules."""
    svg = link_qr_svg("https://trace.example.com/l/abc123")
    assert svg.startswith("<svg")
    assert "xmlns" in svg
    assert 'stroke="#000"' in svg
    assert 'fill="#fff"' in svg
    assert "<?xml" not in svg


def test_encodes_the_given_url_deterministically():
    """Render identical SVG markup for the same URL and different markup for another."""
    first = link_qr_svg("https://trace.example.com/l/abc123")
    again = link_qr_svg("https://trace.example.com/l/abc123")
    other = link_qr_svg("https://trace.example.com/l/zzzzzz")
    assert first == again
    assert first != other
