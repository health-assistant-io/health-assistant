import re


def sanitize_svg(svg_content: str) -> str:
    """
    Sanitizes and optimizes SVG content for use as a category icon.
    Removes potentially malicious scripts and ensures Lucide-like styling compatibility.
    """
    if not svg_content:
        return ""

    # 1. Basic security sanitization: strip scripts and event handlers
    svg = re.sub(
        r"<script.*?>.*?</script>", "", svg_content, flags=re.DOTALL | re.IGNORECASE
    )
    svg = re.sub(r'on\w+=".*?"', "", svg, flags=re.IGNORECASE)

    # 2. Optimization for dynamic scaling
    # Remove hardcoded width/height to allow CSS/Parent scaling
    svg = re.sub(r'width="[^"]*"', "", svg, flags=re.IGNORECASE)
    svg = re.sub(r'height="[^"]*"', "", svg, flags=re.IGNORECASE)

    # 3. Ensure theme-aware styling attributes are present if not specified
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
