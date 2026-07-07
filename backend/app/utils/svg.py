"""SVG sanitization for AI-generated category icons.

Defends against XSS in AI-generated SVG markup. Strips:

- Dangerous elements entirely (``<script>``, ``<foreignObject>``) — both
  paired and self-closing forms, case-insensitively.
- All ``on*`` event-handler attributes regardless of quoting style
  (double-quoted, single-quoted, or unquoted).
- Script-URL attributes (``javascript:``, ``vbscript:``, ``data:text/html``)
  in ``href`` / ``xlink:href``.

The optimization passes (injecting default Lucide-like stroke / fill /
viewBox attributes) are preserved.
"""

import re


# Paired dangerous elements: <script>...</script>, <foreignObject>...</foreignObject>
_PAIRED_DANGEROUS = re.compile(
    r"<\s*(script|foreignObject|foreignobject)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.DOTALL | re.IGNORECASE,
)

# Self-closing dangerous elements: <script .../>, <foreignObject .../>
_SELF_CLOSED_DANGEROUS = re.compile(
    r"<\s*(script|foreignObject|foreignobject)\b[^>]*/\s*>",
    re.IGNORECASE,
)

# All on* event handler attributes in every quoting form.
#   onfoo="..."   onfoo='...'   onfoo=bar   onfoo = "..."
# The alternation matches double-quoted, single-quoted, then unquoted values
# (unquoted = anything up to the next whitespace or tag-closing bracket).
_EVENT_HANDLER_ATTRS = re.compile(
    r"""\s+on[a-zA-Z]+\s*=\s*  # the attribute name + =
        (?:
            "[^"]*"            # double-quoted value
            | '[^']*'          # single-quoted value
            | [^\s>]+          # unquoted value (no spaces, no '>')
        )""",
    re.IGNORECASE | re.VERBOSE,
)

# Script-URL protocols embedded in href / xlink:href attributes. These can
# execute in some SVG renderers (browsers strip most, but not all consumers do).
_SCRIPT_URL_ATTRS = re.compile(
    r"""(?i)\s+(?:xlink:)?href\s*=\s*
        (?:
            "(?:javascript|vbscript|data:text/html)[^"]*"
            | '(?:javascript|vbscript|data:text/html)[^']*'
            | (?:javascript|vbscript|data:text/html)[^\s>]*
        )""",
    re.IGNORECASE | re.VERBOSE,
)


def sanitize_svg(svg_content: str) -> str:
    """
    Sanitizes and optimizes SVG content for use as a category icon.
    Removes potentially malicious scripts/event handlers and ensures
    Lucide-like styling compatibility.
    """
    if not svg_content:
        return ""

    svg = svg_content

    # 1. Security: strip dangerous elements entirely (script, foreignObject).
    #    Run repeatedly in case of nested/degenerate input.
    for _ in range(3):
        prev = svg
        svg = _PAIRED_DANGEROUS.sub("", svg)
        svg = _SELF_CLOSED_DANGEROUS.sub("", svg)
        if svg == prev:
            break

    # 2. Security: strip all on* event handlers regardless of quoting.
    svg = _EVENT_HANDLER_ATTRS.sub("", svg)

    # 3. Security: strip javascript:/vbscript:/data:text/html URLs.
    svg = _SCRIPT_URL_ATTRS.sub("", svg)

    # 4. Optimization for dynamic scaling
    # Remove hardcoded width/height to allow CSS/Parent scaling
    svg = re.sub(r'width="[^"]*"', "", svg, flags=re.IGNORECASE)
    svg = re.sub(r'height="[^"]*"', "", svg, flags=re.IGNORECASE)

    # 5. Ensure theme-aware styling attributes are present if not specified
    if 'stroke="currentColor"' not in svg:
        svg = svg.replace("<svg", '<svg stroke="currentColor"')

    # Only add default fill="none" if no fill is specified anywhere in the SVG
    if 'fill="' not in svg.lower():
        svg = svg.replace("<svg", '<svg fill="none"')

    if 'stroke-width="2"' not in svg:
        svg = svg.replace("<svg", '<svg stroke-width="2"')
    if 'stroke-linecap="round"' not in svg:
        svg = svg.replace("<svg", '<svg stroke-linecap="round"')
    if 'stroke-linejoin="round"' not in svg:
        svg = svg.replace("<svg", '<svg stroke-linejoin="round"')

    # Ensure viewBox exists, if not try to add a default one or preserve existing
    if 'viewbox="' not in svg.lower():
        svg = svg.replace("<svg", '<svg viewBox="0 0 24 24"')

    return svg
