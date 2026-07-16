/**
 * CodeBadge — a compact coded-identifier display: a monospace code, a tiny
 * uppercase system badge, an optional external-lookup link, and a copy button.
 *
 * Replaces the raw string dump of `code` + `coding_system` in detail views.
 * Designed for LOINC / SNOMED / CVX / ATC / ICD-10 / FMA / MeSH codes, but
 * works for any `system` string. When `code` is empty, renders a muted dash.
 *
 * Clickability: the external link is a real `<a target="_blank">` with
 * `rel="noopener noreferrer"` so Cmd/Ctrl-click opens a new tab. The copy
 * button uses {@link CopyButton}. Both are keyboard-focusable.
 */
import React from 'react';
import { ExternalLink } from 'lucide-react';
import { CopyButton } from './CopyButton';

export type CodeSystemKey =
  | 'loinc'
  | 'snomed'
  | 'cvx'
  | 'atc'
  | 'icd10'
  | 'fma'
  | 'mesh'
  | 'custom';

/** Uppercased short label shown next to the code. `custom`/unknown → none. */
const SYSTEM_LABEL: Record<CodeSystemKey, string> = {
  loinc: 'LOINC',
  snomed: 'SNOMED',
  cvx: 'CVX',
  atc: 'ATC',
  icd10: 'ICD-10',
  fma: 'FMA',
  mesh: 'MeSH',
  custom: '',
};

/** Canonical external lookup URL per system. `custom`/mesh → none (MeSH has
 *  no single stable public resolver; callers may pass an explicit href). */
const SYSTEM_HREF: Partial<Record<CodeSystemKey, (code: string) => string>> = {
  loinc: (c) => `https://loinc.org/${encodeURIComponent(c)}`,
  snomed: (c) =>
    `https://browser.ihtsdotools.org/?perspective=full&conceptId1=${encodeURIComponent(c)}&edition=MAIN/SNOMEDCT&release=&languages=en`,
  cvx: (c) =>
    `https://www.hl7.org/fhir/valueset-vaccine-code.html#${encodeURIComponent(c)}`,
  atc: (c) => `https://www.whocc.no/atc_ddd_index/?code=${encodeURIComponent(c)}`,
  icd10: (c) => `https://icd.who.int/browse10/2016/en#/${encodeURIComponent(c)}`,
  fma: (c) => `https://bioportal.bioontology.org/ontologies/FMA/?p=classes&conceptid=${encodeURIComponent(c)}`,
};

interface CodeBadgeProps {
  code: string | null | undefined;
  system?: CodeSystemKey | string | null;
  /** Override or supply an external link when a system has no default
   *  resolver (e.g. MeSH). A function receives the code; a string is used as-is. */
  lookupHref?: string | ((code: string) => string);
  /** Show the copy affordance (default true). */
  copyable?: boolean;
  /** Hide the system badge label (default false). */
  hideSystemBadge?: boolean;
  className?: string;
}

function resolveSystem(raw: string | null | undefined): CodeSystemKey {
  if (!raw) return 'custom';
  const k = raw.trim().toLowerCase();
  return (k in SYSTEM_LABEL ? (k as CodeSystemKey) : 'custom');
}

export const CodeBadge: React.FC<CodeBadgeProps> = ({
  code,
  system,
  lookupHref,
  copyable = true,
  hideSystemBadge = false,
  className = '',
}) => {
  const codeStr = code ? String(code).trim() : '';
  if (!codeStr) {
    return <span className={`text-gray-400 ${className}`}>—</span>;
  }

  const sysKey = resolveSystem(typeof system === 'string' ? system : null);
  const sysLabel = SYSTEM_LABEL[sysKey];
  const resolver =
    typeof lookupHref === 'function'
      ? lookupHref
      : typeof lookupHref === 'string'
        ? () => lookupHref
        : SYSTEM_HREF[sysKey];
  const href = resolver ? resolver(codeStr) : null;

  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      {sysLabel && !hideSystemBadge && (
        <span
          className="text-[9px] font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400"
          aria-hidden
        >
          {sysLabel}
        </span>
      )}
      <span className="font-mono text-xs bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-100 px-1.5 py-0.5 rounded">
        {codeStr}
      </span>
      {href && (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
          aria-label={`${sysLabel || 'Code'} ${codeStr}, opens external`}
          title={`${sysLabel || 'Code'} ${codeStr} — opens external reference`}
        >
          <ExternalLink className="w-3.5 h-3.5" aria-hidden />
        </a>
      )}
      {copyable && <CopyButton value={codeStr} size={13} />}
    </span>
  );
};

export default CodeBadge;
