/**
 * Format detection + conversion for rich-text fields.
 *
 * The catalog stores long-text fields (description, info, indications,
 * contraindications, dosage_info) as either HTML (produced by the Quill
 * editor) or Markdown (produced by LLMs / AI magic-fill / catalog imports).
 * This module detects which and converts Markdown → HTML so the editor always
 * receives HTML, and the presentation layer renders whichever format is stored.
 */

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
