"""Preserve authored landing HTML and inspect reserved capture controls."""

from html import escape, unescape
from html.parser import HTMLParser
import re
from markupsafe import Markup

#: Default sharing controls used only when no template HTML was supplied.
DEFAULT_TEMPLATE = """<button id="enable-button" type="button"></button>
<button id="stop-button" type="button"></button>"""

#: Maximum UTF-8 template size before rendering: 10 MB (10,000,000 bytes).
MAX_TEMPLATE_BYTES = 10_000_000
#: Allow three-byte percent encoding per source byte plus space for other form fields.
MAX_TEMPLATE_REQUEST_BYTES = 3 * MAX_TEMPLATE_BYTES + 64 * 1024
#: Reserved identifiers bound to the visitor's sharing and stop actions.
CONTROL_IDS = frozenset({"enable-button", "disable-button", "stop-button"})
#: HTML void elements used when inspecting controls without rewriting the source.
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def validate_template(source):
    """Check the UTF-8 size of authored HTML without modifying its contents.

    Args:
        source: Administrator-authored HTML, including full documents or fragments.

    Returns:
        The exact input string, preserving tags, attributes, entities, and whitespace.

    Raises:
        ValueError: The source is not text or exceeds the 10 MB UTF-8 template limit.
    """
    if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise ValueError("HTML templates must be 10 MB or smaller.")
    return source


class ControlExtractor(HTMLParser):
    """Inspect reserved ID-bound controls without evaluating template markup.

    Attributes:
        content: Template fragments outside reserved sharing controls.
        controls: First extracted specification for each reserved control identifier.
        current: Control specification currently being collected, or None.
        depth: Element nesting depth within the current reserved control.
    """

    def __init__(self, source):
        """Parse the supplied static HTML into content and reserved-control specifications.

        Args:
            source: Complete source text to parse or validate without executing it.
        """
        super().__init__(convert_charrefs=False)
        self.content, self.controls, self.current, self.depth = [], {}, None, 0
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        """Process an opening HTML tag using this parser’s allowlist and state.

        Args:
            tag: Lowercase element name supplied by HTMLParser.
            attrs: HTML attribute name/value pairs supplied by HTMLParser.
        """
        attributes = dict(attrs)
        if self.current is not None:
            if tag not in VOID_TAGS:
                self.depth += 1
            return
        identifier = attributes.get("id")
        if identifier in CONTROL_IDS:
            self.current = {
                "id": identifier,
                "tag": tag,
                "text": [],
                "value": attributes.get("value", attributes.get("alt", "")),
            }
            self.depth = 1
            if tag in VOID_TAGS:
                self.complete()
        else:
            self.content.append(self.get_starttag_text())

    def complete(self):
        """Save the first reserved control with this identifier and reset parsing state."""
        self.controls.setdefault(self.current["id"], self.current)
        self.current, self.depth = None, 0

    def handle_endtag(self, tag):
        """Close the current permitted element without restoring blocked content.

        Args:
            tag: Lowercase element name supplied by HTMLParser.
        """
        if self.current is not None:
            self.depth -= 1
            if self.depth == 0:
                self.complete()
        elif tag not in VOID_TAGS:
            self.content.append(f"</{tag}>")

    def handle_data(self, data):
        """Collect original text for the current content fragment or reserved control.

        Args:
            data: Literal text supplied by the HTML parser.
        """
        (self.current["text"] if self.current is not None else self.content).append(
            data
        )

    def handle_entityref(self, name):
        """Preserve a named HTML entity as literal template content.

        Args:
            name: Name of the option, entity, or field being processed.
        """
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        """Preserve a numeric HTML entity as literal template content.

        Args:
            name: Name of the option, entity, or field being processed.
        """
        self.handle_data(f"&#{name};")

    def close(self):
        """Finalize parsing and save an unfinished reserved control if present."""
        super().close()
        if self.current is not None:
            self.complete()


def template_controls(source):
    """Extract reserved sharing controls without evaluating HTML or scripts.

    Args:
        source: Complete source text to parse or validate without executing it.

    Returns:
        Reserved control identifiers mapped to extracted tag and label details.
    """
    return ControlExtractor(source).controls


def render_control(spec, preview=False):
    """Render one reserved control with safe labels and preview behavior.

    Args:
        spec: Extracted reserved-control mapping containing its tag, identifier, and text.
        preview: Whether to disable capture controls in the administrator preview.

    Returns:
        Escaped Markup for one control, safe for insertion into Jinja output.
    """
    tag = spec["tag"]
    safe_flow = {
        "button",
        "input",
        "a",
        "div",
        "span",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "nav",
        "label",
        "strong",
        "em",
        "b",
        "i",
        "small",
        "pre",
        "code",
        "blockquote",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "figure",
        "figcaption",
        "address",
        "output",
        "summary",
    }
    if tag not in safe_flow and not (
        "-" in tag and re.fullmatch(r"[a-z][a-z0-9-]*", tag)
    ):
        tag = "button"
    label = unescape("".join(spec["text"]).strip() or spec["value"])
    label = label or ("Share" if spec["id"] == "enable-button" else "Stop")
    attrs = f' id="{escape(spec["id"], quote=True)}" data-custom-label="true"'
    if tag in {"button", "input"}:
        attrs += ' type="button"' + (" disabled" if preview else "")
    else:
        attrs += ' role="button" tabindex="0"' + (
            ' aria-disabled="true"' if preview else ""
        )
    if tag == "input":
        return Markup(f'<input{attrs} value="{escape(label, quote=True)}">')
    return Markup(f"<{tag}{attrs}>{escape(label)}</{tag}>")


def template_document(source):
    """Return authored HTML directly, using default controls only for an empty template.

    Args:
        source: Administrator-authored HTML served without Jinja evaluation or rewriting.

    Returns:
        Original validated source, or the default controls when the source is empty.
    """
    return validate_template(source) or DEFAULT_TEMPLATE
