"""Test exact HTML preservation, size validation, and reserved capture controls."""

import pytest

from flaretrace.links.templates import (
    ControlExtractor,
    DEFAULT_TEMPLATE,
    MAX_TEMPLATE_BYTES,
    render_control,
    validate_template,
    template_controls,
    template_document,
)


@pytest.mark.parametrize("source", [None, 10, "x" * (MAX_TEMPLATE_BYTES + 1)])
def test_invalid_source_is_rejected(source):
    """Verify invalid source is rejected.

    Args:
        source: Complete source text to parse or validate without executing it.
    """
    with pytest.raises(ValueError):
        validate_template(source)


@pytest.mark.parametrize("character", ["x", "é"])
def test_template_limit_counts_utf8_bytes(character):
    """Accept exactly 10 MB and reject another character using encoded byte length.

    Args:
        character: Single-byte or multibyte text used to exercise the byte boundary.
    """
    source = character * (10_000_000 // len(character.encode("utf-8")))
    assert validate_template(source) == source
    with pytest.raises(ValueError, match="HTML templates must be 10 MB or smaller"):
        validate_template(source + character)


def test_validation_preserves_all_authored_markup():
    """Preserve scripts, custom elements, attributes, and external URLs byte for byte."""
    source = '<script><iframe>secret</iframe>unsafe</script><p hidden onclick="bad()" data-safe="yes">A &amp; B</p><img src="https://example.com/x"><input id="enable-button" disabled required><x-control id="stop-button">Stop</x-control><style>p{color:red}</style><br/>'
    assert validate_template(source) == source
    assert validate_template(DEFAULT_TEMPLATE) == DEFAULT_TEMPLATE


def test_validation_preserves_whitespace_and_original_tag_syntax():
    """Leave authored spacing, casing, self-closing tags, and entities unchanged."""
    source = ' \n<P class=original/><IMG SRC="data:image/png;base64,AAAA"/> &copy;\t\n'
    assert validate_template(source) == source
    assert validate_template(" \n\t") == " \n\t"


def test_control_extraction_preserves_nested_labels_and_first_duplicate():
    """Verify control extraction preserves nested labels and first duplicate."""
    source = '<p>Context</p><button id="enable-button"><b>A &amp; B &#33;</b><br> Go</button><button id="enable-button">Duplicate</button><input id="stop-button" value="End"><hr>'
    parsed = ControlExtractor(source)
    assert parsed.controls["enable-button"]["text"] == [
        "A ",
        "&amp;",
        " B ",
        "&#33;",
        " Go",
    ]
    assert parsed.controls["stop-button"]["value"] == "End"
    assert "<p>Context</p>" in "".join(parsed.content)
    assert "Duplicate" not in "".join(parsed.controls["enable-button"]["text"])
    assert template_controls('<button id="enable-button">Unclosed')["enable-button"][
        "text"
    ] == ["Unclosed"]


@pytest.mark.parametrize(
    "tag, preview, expected",
    [
        (
            "button",
            False,
            '<button id="enable-button" data-custom-label="true" type="button">Go &amp; see</button>',
        ),
        (
            "input",
            True,
            '<input id="enable-button" data-custom-label="true" type="button" disabled value="Go &amp; see">',
        ),
        (
            "div",
            True,
            '<div id="enable-button" data-custom-label="true" role="button" tabindex="0" aria-disabled="true">Go &amp; see</div>',
        ),
        (
            "img",
            False,
            '<button id="enable-button" data-custom-label="true" type="button">Go &amp; see</button>',
        ),
        (
            "custom-control",
            False,
            '<custom-control id="enable-button" data-custom-label="true" role="button" tabindex="0">Go &amp; see</custom-control>',
        ),
    ],
)
def test_rendered_controls_are_accessible_and_escaped(tag, preview, expected):
    """Verify rendered controls are accessible and escaped.

    Args:
        tag: Lowercase element name supplied by HTMLParser.
        preview: Whether to disable capture controls in the administrator preview.
        expected: Expected normalized value or public response for this parametrized case.
    """
    spec = {"tag": tag, "id": "enable-button", "text": ["Go &amp; see"], "value": ""}
    assert str(render_control(spec, preview)) == expected


def test_fallback_control_labels_and_original_document():
    """Retain legacy control labels and serve original HTML without Jinja evaluation."""
    assert ">Share<" in render_control(
        {"tag": "button", "id": "enable-button", "text": [], "value": ""}
    )
    assert ">Stop<" in render_control(
        {"tag": "button", "id": "stop-button", "text": [], "value": ""}
    )
    source = "<h1>{{ 7*7 }}</h1><script>customBehavior()</script>"
    assert template_document(source) == source
    assert template_document("") == DEFAULT_TEMPLATE
