/**
 * Format detection + conversion for rich-text fields.
 *
 * The catalog stores long-text fields (description, info, indications,
 * contraindications, dosage_info) as either HTML (produced by the Quill
 * editor) or Markdown (produced by LLMs / AI magic-fill / catalog imports).
 * This module detects which and converts Markdown → HTML so the editor always
 * receives HTML, and the presentation layer renders whichever format is stored.
 */

import { marked } from 'marked';

export type TextFormat = 'html' | 'markdown' | 'plain';

/** Matches an HTML tag (open or close) — e.g. ``<p>``, ``</div>``, ``<br/>``. */
const HTML_TAG_RE = /<\/?[a-z][\s\S]*?>/i;

/**
 * Matches common Markdown constructs:
 *  - ATX headings (``#``, ``##``, …)
 *  - bullet / ordered list items, blockquotes, fenced code, hr, tables
 *  - bold/italic markers, inline code, links
 */
const MARKDOWN_RE = new RegExp(
  [
    '(^|\\n)\\s*#{1,6}\\s', // headings
    '(^|\\n)\\s*[-*+]\\s', // bullet lists
    '(^|\\n)\\s*\\d+\\.\\s', // ordered lists
    '(^|\\n)\\s*>\\s', // blockquotes
    '(^|\\n)```', // fenced code
    '(^|\\n)---', // horizontal rule
    '(^|\\n)\\|', // tables
    '\\*\\*[^*]+\\*\\*', // bold
    '__[^_]+__', // bold alt
    '`[^`]+`', // inline code
    '\\[[^\\]]+\\]\\([^)]+\\)', // links
  ].join('|'),
);

/**
 * Detect whether a string is HTML, Markdown, or plain text.
 *
 * HTML wins over Markdown (a Markdown doc won't contain ``<tag>`` patterns
 * unless it's actually HTML-with-inline-md, which is rare for stored fields).
 */
export function detectTextFormat(text: string | null | undefined): TextFormat {
  if (!text || !text.trim()) return 'plain';
  if (HTML_TAG_RE.test(text)) return 'html';
  if (MARKDOWN_RE.test(text)) return 'markdown';
  return 'plain';
}

/** True if the value contains any non-whitespace content. */
export function hasTextContent(text: string | null | undefined): boolean {
  return !!text && text.trim().length > 0;
}

/**
 * Coerce a FHIR text field to a plain string. Accepts a string directly or a
 * CodeableConcept-shaped object ({@code {text}}, {@code {coding:[{display}]}}),
 * which some endpoints/serializers emit (e.g. Observation.interpretation).
 * Low-level util so adapters, listing pages, and biomarker status all share it.
 */
export function codeableText(v: unknown): string | undefined {
  if (!v) return undefined;
  if (typeof v === 'string') return v;
  if (typeof v === 'object') {
    const obj = v as { text?: string; coding?: Array<{ display?: string }> };
    return obj.text || obj.coding?.[0]?.display || undefined;
  }
  return undefined;
}

/**
 * Collapse a rich-text value (HTML / Markdown / plain) to a single plain-text
 * line. Markdown is parsed to HTML via {@link marked}, then HTML is stripped
 * via the DOM (handles entities + nested tags); the result has all whitespace
 * (incl. block boundaries) collapsed to single spaces. Use this for compact
 * one-line displays like list rows / subtitles where block rendering isn't
 * appropriate — for full rich rendering use {@link FormattedText}.
 */
export function toPlainText(text: unknown): string {
  if (!text) return '';
  let s = String(text);
  // Normalise Markdown → HTML first so a single DOM strip handles both.
  // `marked` is synchronous by default and gracefully passes through text that
  // isn't really markdown, so this never throws for odd input.
  if (detectTextFormat(s) === 'markdown') {
    try {
      s = marked.parse(s) as string;
    } catch {
      // keep the original string on parse failure
    }
  }
  if (detectTextFormat(s) === 'html' && typeof document !== 'undefined') {
    const el = document.createElement('div');
    el.innerHTML = s;
    s = el.textContent || el.innerText || '';
  }
  return s.replace(/\s+/g, ' ').trim();
}

/**
 * A plain-text snippet (via {@link toPlainText}) truncated to `maxLen` chars
 * with an ellipsis. For short secondary display fields derived from rich-text
 * sources (e.g. an examination notes preview in a list row / card).
 */
export function toSnippet(
  text: string | null | undefined,
  maxLen = 120,
): string | undefined {
  const plain = toPlainText(text);
  if (!plain) return undefined;
  if (plain.length <= maxLen) return plain;
  return `${plain.slice(0, maxLen).trimEnd()}…`;
}

