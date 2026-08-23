import DOMPurify from 'dompurify';

const ALLOWED_TAGS = [
  'a', 'b', 'blockquote', 'br', 'code', 'div', 'em', 'h1', 'h2', 'h3', 'h4',
  'h5', 'h6', 'hr', 'i', 'li', 'ol', 'p', 'pre', 'small', 'span', 'strong',
  'sub', 'sup', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'u', 'ul', 'img',
];

const ALLOWED_ATTR = ['href', 'title', 'target', 'rel', 'src', 'alt', 'colspan', 'rowspan', 'class'];

export function sanitizeHtml(html: string | null | undefined): string {
  if (!html) return '';
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
    FORBID_ATTR: ['style', 'srcset', 'formaction'],
  });
}

export function sanitizeSvg(svg: string | null | undefined): string {
  if (!svg) return '';
  return DOMPurify.sanitize(svg, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ['script', 'foreignObject'],
    FORBID_ATTR: ['onload', 'onclick', 'onerror', 'onmouseover'],
  });
}

export default sanitizeHtml;
