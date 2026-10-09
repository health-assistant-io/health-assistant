import { describe, expect, it } from 'vitest';
import {
  chatUrlTransform,
  citationsToMarkdown,
  isCitationHref,
  rewriteApiLink,
} from '../markdownLinks';

describe('citationsToMarkdown', () => {
  it('rewrites structured type=uuid references into citation links', () => {
    expect(citationsToMarkdown('See [biomarker=abc-123] for details')).toBe(
      'See [biomarker](citation://biomarker=abc-123) for details',
    );
  });

  it('rewrites Ref-prefixed and truncated references', () => {
    expect(citationsToMarkdown('Source: [Ref: medication=m1...]')).toBe(
      'Source: [medication](citation://medication=m1...)',
    );
  });

  it('rewrites bare bracketed tool names', () => {
    expect(citationsToMarkdown('Checked [get_recent_observations] already')).toBe(
      'Checked [observations](citation://get_recent_observations) already',
    );
  });

  it('rewrites bare get_* tool names in running text', () => {
    expect(citationsToMarkdown('I used get_medications to look it up')).toBe(
      'I used [medications](citation://get_medications) to look it up',
    );
  });

  it('leaves ordinary markdown untouched', () => {
    const text = 'Plain **bold** and [a link](https://example.com/x)';
    expect(citationsToMarkdown(text)).toBe(text);
  });
});

describe('chatUrlTransform', () => {
  it('keeps citation scheme hrefs', () => {
    expect(chatUrlTransform('citation://get_lab_results')).toBe('citation://get_lab_results');
  });

  it('keeps http, https and mailto', () => {
    expect(chatUrlTransform('https://example.com/a?b=1')).toBe('https://example.com/a?b=1');
    expect(chatUrlTransform('http://example.com')).toBe('http://example.com');
    expect(chatUrlTransform('mailto:someone@example.com')).toBe('mailto:someone@example.com');
  });

  it('keeps internal routes and anchors', () => {
    expect(chatUrlTransform('/medications/details/1')).toBe('/medications/details/1');
    expect(chatUrlTransform('#section')).toBe('#section');
  });

  it('drops javascript: and data: urls', () => {
    expect(chatUrlTransform('javascript:alert(1)')).toBe('');
    expect(chatUrlTransform('data:text/html;base64,PHNjcmlwdD4=')).toBe('');
    expect(chatUrlTransform('vbscript:msgbox')).toBe('');
  });
});

describe('isCitationHref / rewriteApiLink', () => {
  it('detects citation hrefs case-insensitively', () => {
    expect(isCitationHref('citation://x')).toBe(true);
    expect(isCitationHref('CITATION://x')).toBe(true);
    expect(isCitationHref('https://x')).toBe(false);
    expect(isCitationHref(undefined)).toBe(false);
  });

  it('rewrites api entity links to ui routes', () => {
    expect(rewriteApiLink('https://app.example.com/api/v1/fhir/medication/42')).toEqual({
      uiPath: '/medications/details/42',
    });
    expect(rewriteApiLink('/api/v1/biomarkers/99')).toEqual({ uiPath: '/biomarkers/details/99' });
    expect(rewriteApiLink('/api/v1/fhir/observation/7')).toEqual({ uiPath: '/biomarkers' });
    expect(rewriteApiLink('https://example.com/some/page')).toBeNull();
  });
});
