import { describe, expect, it } from 'vitest';
import { sanitizeHtml, sanitizeSvg } from './sanitize';

describe('sanitizeHtml (audit 2026-08 FE-H1/H3)', () => {
  it('strips script tags and event handlers', () => {
    const evil = '<div onclick="steal()"><img src=x onerror="steal()">ok</div><script>steal()</script>';
    const out = sanitizeHtml(evil);
    expect(out).not.toContain('onerror');
    expect(out).not.toContain('onclick');
    expect(out).not.toContain('<script');
    expect(out).toContain('ok');
  });

  it('blocks javascript: URIs', () => {
    const out = sanitizeHtml('<a href="javascript:steal()">click</a>');
    expect(out).not.toContain('javascript:');
  });

  it('keeps benign rich text', () => {
    const benign = '<p>Hello <strong>world</strong> <a href="https://example.com" target="_blank" rel="noopener noreferrer">link</a></p>';
    expect(sanitizeHtml(benign)).toContain('<strong>world</strong>');
    expect(sanitizeHtml(benign)).toContain('https://example.com');
  });
});

describe('sanitizeSvg (audit 2026-08 FE-M6)', () => {
  it('strips onload handlers and foreignObject', () => {
    const evil = '<svg onload="steal()"><foreignObject><body>x</body></foreignObject><circle r="5"/></svg>';
    const out = sanitizeSvg(evil);
    expect(out).not.toContain('onload');
    expect(out).not.toContain('foreignObject');
    expect(out).toContain('circle');
  });
});
