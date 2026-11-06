import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ShieldCheck } from 'lucide-react';
import { Button } from '../ui/Button';

export interface ConsentDelta {
  versionId: string;
  incomingDigest: string;
  runningDigest: string | null;
  version: { from: string | null; to: string | null };
  capabilitiesAdded: string[];
  capabilitiesRemoved: string[];
  capabilitiesUnchanged: string[];
  keptGrants: string[];
  requiresReConsent: boolean;
  status: string | null;
  disclosureKey: string;
  disclosure: string;
}

interface Props {
  delta: ConsentDelta;
  onDecide: (versionId: string, decisions: Record<string, 'granted' | 'denied'>) => Promise<void>;
  onDone?: () => void;
}

function shortDigest(digest: string): string {
  return `${digest.slice(0, 8)}…${digest.slice(-6)}`;
}

/**
 * The plan-37 8C change-summary surface (plan 7 W3): renders the capability
 * delta between the running digest and the incoming one. New capabilities
 * force a decision each; unchanged capabilities keep their grants (shown as
 * carried); removed ones are reported without re-prompting. The tier's
 * disclosure renders VERBATIM from the charter — it is law text, never app
 * copy.
 */
export default function ConsentDeltaView({ delta, onDecide, onDone }: Props) {
  const { t } = useTranslation();
  const [decisions, setDecisions] = useState<Record<string, 'granted' | 'denied'>>({});
  const [submitting, setSubmitting] = useState(false);

  const undecided = useMemo(
    () => delta.capabilitiesAdded.filter((capability) => !decisions[capability]),
    [delta.capabilitiesAdded, decisions],
  );
  const ready = undecided.length === 0 && delta.capabilitiesAdded.length > 0;

  const decide = (capability: string, value: 'granted' | 'denied') =>
    setDecisions((current) => ({ ...current, [capability]: value }));

  const submit = async () => {
    if (!ready || submitting) return;
    setSubmitting(true);
    try {
      await onDecide(delta.versionId, decisions);
      onDone?.();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section
      aria-label={t('consent.delta.title', 'Consent change summary')}
      data-testid="consent-delta-view"
      className="space-y-4"
    >
      <header className="flex items-center gap-2">
        <ShieldCheck className="h-5 w-5 text-primary" aria-hidden />
        <h3 className="text-base font-semibold">
          {t('consent.delta.title', 'Consent change summary')}
        </h3>
        <span className="text-sm text-muted-foreground">
          {delta.version.from
            ? t('consent.delta.upgrade', '{{from}} → {{to}}', {
                from: delta.version.from,
                to: delta.version.to,
              })
            : t('consent.delta.install', 'install {{to}}', { to: delta.version.to })}
        </span>
      </header>

      <p className="text-sm text-muted-foreground">
        {t(
          'consent.delta.digestLine',
          'Content digest {{incoming}} (running: {{running}})',
          {
            incoming: shortDigest(delta.incomingDigest),
            running: delta.runningDigest ? shortDigest(delta.runningDigest) : '—',
          },
        )}
      </p>

      {delta.capabilitiesAdded.length > 0 && (
        <div className="rounded-md border border-warning/40 bg-warning/5 p-3">
          <h4 className="text-sm font-semibold">
            {t(
              'consent.delta.newCapabilities',
              'New capabilities — your decision is required',
            )}
          </h4>
          <ul className="mt-2 space-y-2" data-testid="consent-added-list">
            {delta.capabilitiesAdded.map((capability) => (
              <li
                key={capability}
                className="flex items-center justify-between gap-2"
                data-testid={`consent-capability-${capability}`}
              >
                <code className="text-sm">{capability}</code>
                <span className="flex gap-2">
                  <Button
                    size="sm"
                    variant={decisions[capability] === 'granted' ? 'default' : 'outline'}
                    aria-pressed={decisions[capability] === 'granted'}
                    onClick={() => decide(capability, 'granted')}
                  >
                    {t('consent.delta.grant', 'Grant')}
                  </Button>
                  <Button
                    size="sm"
                    variant={decisions[capability] === 'denied' ? 'destructive' : 'outline'}
                    aria-pressed={decisions[capability] === 'denied'}
                    onClick={() => decide(capability, 'denied')}
                  >
                    {t('consent.delta.deny', 'Deny')}
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {delta.capabilitiesUnchanged.length > 0 && (
        <div className="rounded-md border p-3">
          <h4 className="text-sm font-semibold">
            {t('consent.delta.unchanged', 'Unchanged — grants carried over')}
          </h4>
          <ul className="mt-2 flex flex-wrap gap-2" data-testid="consent-unchanged-list">
            {delta.capabilitiesUnchanged.map((capability) => (
              <li
                key={capability}
                className="rounded bg-muted px-2 py-0.5 text-xs"
                data-testid={`consent-kept-${capability}`}
              >
                {capability}
              </li>
            ))}
          </ul>
        </div>
      )}

      {delta.capabilitiesRemoved.length > 0 && (
        <div className="text-sm text-muted-foreground" data-testid="consent-removed-list">
          {t(
            'consent.delta.removed',
            'No longer declared: {{capabilities}}',
            { capabilities: delta.capabilitiesRemoved.join(', ') },
          )}
        </div>
      )}

      <blockquote
        className="border-l-4 border-border pl-3 text-sm italic text-muted-foreground"
        data-testid="consent-disclosure"
        data-disclosure-key={delta.disclosureKey}
      >
        {delta.disclosure}
      </blockquote>

      <div className="flex items-center gap-3">
        <Button disabled={!ready || submitting} onClick={submit} data-testid="consent-submit">
          {submitting
            ? t('consent.delta.submitting', 'Recording…')
            : t('consent.delta.submit', 'Record decisions')}
        </Button>
        {delta.capabilitiesAdded.length > 0 && undecided.length > 0 && (
          <span className="text-sm text-muted-foreground">
            {t(
              'consent.delta.pending',
              '{{count}} capability(ies) still undecided',
              { count: undecided.length },
            )}
          </span>
        )}
      </div>
    </section>
  );
}
