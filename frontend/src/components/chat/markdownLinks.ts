import { defaultUrlTransform } from 'react-markdown';

/**
 * Rewrite the model's inline source references into `citation://` markdown
 * links so the `a` renderer can turn them into CitationButton chips.
 *
 * Recognizes (in order):
 *   - ``[Ref: type=uuid]`` / ``[type=uuid...]`` — structured references
 *   - ``[tool_name]`` — bare tool-name references
 *   - bare ``get_*`` / ``get_recent_*`` tool names in running text
 *
 * Extracted verbatim from the pre-library AIChatInterface markdown path.
 */
export function citationsToMarkdown(text: string): string {
  let out = text;
  out = out.replace(
    /\[(?:Ref:\s*)?([a-z_]+)=([a-z0-9-_]+)(\.\.\.)?\]/gi,
    (_match, type: string, uuid: string, truncated: string) =>
      `@@@REF:${type}=${uuid}${truncated ? '...' : ''}@@@`,
  );
  out = out.replace(/\[(?:Ref:\s*)?([a-z_0-9]+)\]/gi, (match, name: string) => {
    if (name.includes('@@@REF:')) return match;
    return `@@@REF:${name}@@@`;
  });
  out = out.replace(/(^|\s)(get_[a-z_0-9]+)(?=\s|$)/gi, (_m, space: string, name: string) => {
    return `${space}@@@REF:${name}@@@`;
  });
  out = out.replace(/@@@REF:([^@]+)@@@/g, (_match, ref: string) => {
    const label = ref
      .split('=')[0]
      .replace(/get_|recent_|history|_details/g, '')
      .replace(/_/g, ' ');
    return `[${label}](citation://${ref})`;
  });
  return out;
}

/**
 * Tolerant-but-safe URL transform for chat markdown: keeps the app's
 * `citation://` scheme, http(s)/mailto URLs, and same-document anchors /
 * internal routes, and drops everything else (`javascript:`, `data:`,
 * unknown schemes). PHI never travels in URLs — citations carry only tool
 * names, so nothing sensitive can leak through the kept schemes.
 */
export function chatUrlTransform(url: string): string {
  const lower = url.toLowerCase();
  if (lower.startsWith('citation://')) return url;
  if (url.startsWith('/') || url.startsWith('#')) return url;
  return defaultUrlTransform(url);
}

/** True for hrefs the `a` renderer should paint as a citation chip. */
export function isCitationHref(href: string | undefined): boolean {
  return Boolean(href && href.toLowerCase().includes('citation:'));
}

/** The reference token of a `citation://…` href (after the scheme). */
export function citationRef(href: string): string {
  return href!.split('://').pop() || '';
}

export interface ApiLinkRewrite {
  uiPath: string;
}

/**
 * Map an API entity URL onto the UI route that shows the entity, mirroring
 * the pre-library link rewriting (`/api/v1/fhir/meditation/…` → the
 * medication details page). Returns null when the href is not an API link.
 */
export function rewriteApiLink(href: string): ApiLinkRewrite | null {
  if (!href.includes('/api/v1/')) return null;
  const parts = href.split('/');
  const id = parts[parts.length - 1];
  if (href.includes('/fhir/medication/')) return { uiPath: `/medications/details/${id}` };
  if (href.includes('/biomarkers/')) return { uiPath: `/biomarkers/details/${id}` };
  if (href.includes('/fhir/observation/')) return { uiPath: '/biomarkers' };
  return null;
}
