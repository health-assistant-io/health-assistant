import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import { CloudUpload, PackagePlus } from 'lucide-react';
import { Button, Input, PanelModal } from '@neuronection/assistant-ui';
import { ErrorBanner } from '@neuronection/assistant-ui';
import {
  createSeedSource,
  seedApiError,
  uploadSeedPack,
  type CreateSourcePayload,
  type SeedSourceKind,
  type SeedSourceScope,
} from '../../../services/seedSourcesService';

const KINDS: Array<Exclude<SeedSourceKind, 'bundled'>> = ['file', 'git', 'registry'];
const PRIORITY_DEFAULTS = { system: '100', tenant: '250' } as const;

interface AddSourceWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** SYSTEM_ADMIN may pick either scope; a tenant admin always creates
   * tenant overlays (the backend derives it from the session role). */
  canChooseScope: boolean;
  onCreated: (sourceId: string) => void | Promise<void>;
}

/** The add-source wizard: pick a kind, fill the descriptor (git/registry
 * URL + secret slot (+ pinned ref + subpath for git), or a file upload),
 * register, and — for file sources — upload the first pack so the new
 * source lands with a pending digest. */
export function AddSourceWizard({ open, onOpenChange, canChooseScope, onCreated }: AddSourceWizardProps) {
  const { t } = useTranslation();
  const defaultPriority = PRIORITY_DEFAULTS[canChooseScope ? 'system' : 'tenant'];
  const [kind, setKind] = useState<(typeof KINDS)[number]>('file');
  const [uri, setUri] = useState('');
  const [refSpec, setRefSpec] = useState('');
  const [subpath, setSubpath] = useState('');
  const [authSecretRef, setAuthSecretRef] = useState('');
  const [priority, setPriority] = useState<string>(defaultPriority);
  const [scope, setScope] = useState<SeedSourceScope>('system');
  const [packId, setPackId] = useState('');
  const [packVersion, setPackVersion] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);

  const isFile = kind === 'file';
  const needsUri = kind !== 'file';
  const needsSecret = kind === 'git' || kind === 'registry';
  const effectiveScope: SeedSourceScope = canChooseScope ? scope : 'tenant';
  const priorityBand = useMemo(
    () =>
      effectiveScope === 'tenant'
        ? t('seed_sources.wizard.priority_band_tenant', 'tenant band: 200–999')
        : t('seed_sources.wizard.priority_band_system', 'system band: 0–199 (bundled pins 0)'),
    [effectiveScope, t],
  );

  const reset = () => {
    setKind('file');
    setUri('');
    setRefSpec('');
    setSubpath('');
    setAuthSecretRef('');
    setPriority(defaultPriority);
    setScope('system');
    setPackId('');
    setPackVersion('');
    setFile(null);
    setError(null);
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    const payload: CreateSourcePayload = {
      kind,
      priority: Number(priority) || 0,
    };
    if (canChooseScope) payload.scope = scope;
    if (needsUri) payload.uri = uri;
    if (needsSecret && authSecretRef) payload.auth_secret_ref = authSecretRef;
    if (kind === 'git') {
      if (refSpec) payload.ref_spec = refSpec;
      if (subpath) payload.subpath = subpath;
    }
    try {
      const source = await createSeedSource(payload);
      if (isFile && file) {
        await uploadSeedPack(source.id, file, {
          packId: packId || undefined,
          version: packVersion || undefined,
        });
      }
      toast.success(t('seed_sources.wizard.created', 'Source registered.'));
      await onCreated(source.id);
      reset();
      onOpenChange(false);
    } catch (err) {
      console.error('Failed to create seed source', err);
      setError(
        seedApiError(err) ?? {
          code: 'CREATE_FAILED',
          message: t('seed_sources.wizard.failed', 'Failed to register the source.'),
        },
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <PanelModal
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
      title={t('seed_sources.wizard.title', 'Add a seed source')}
      description={t(
        'seed_sources.wizard.subtitle',
        'A pack is digest-pinned, admin-approved and applied with full provenance.',
      )}
      headerIcon={<PackagePlus className="h-5 w-5 text-blue-500" />}
      footer={
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel', 'Cancel')}
          </Button>
          <Button type="button" loading={busy} disabled={busy} onClick={submit}>
            {isFile && file
              ? t('seed_sources.wizard.create_and_upload', 'Register & upload pack')
              : t('seed_sources.wizard.create', 'Register source')}
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <ErrorBanner message={error ? `${error.code}: ${error.message}` : null} />

        <fieldset className="space-y-1">
          <legend className="text-sm font-semibold text-gray-900 dark:text-dark-text">
            {t('seed_sources.wizard.kind', 'Kind')}
          </legend>
          <div className="flex gap-2">
            {KINDS.map((k) => (
              <button
                key={k}
                type="button"
                aria-pressed={kind === k}
                onClick={() => setKind(k)}
                className={`rounded-lg border px-3 py-1.5 text-sm capitalize ${
                  kind === k
                    ? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                    : 'border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted'
                }`}
              >
                {k}
              </button>
            ))}
          </div>
        </fieldset>

        {needsUri && (
          <Input
            label={t('seed_sources.wizard.uri', 'URL (https-only)')}
            placeholder={kind === 'git' ? 'https://github.com/org/repo.git' : 'https://example.com/packs'}
            value={uri}
            onChange={(e) => setUri(e.target.value)}
          />
        )}
        {kind === 'git' && (
          <>
            <Input
              label={t('seed_sources.wizard.ref', 'Pinned ref (tag/commit)')}
              placeholder="v1.2.3"
              value={refSpec}
              onChange={(e) => setRefSpec(e.target.value)}
            />
            <Input
              label={t('seed_sources.wizard.subpath', 'Subpath inside the repo')}
              placeholder="packs/core"
              value={subpath}
              onChange={(e) => setSubpath(e.target.value)}
            />
          </>
        )}
        {needsSecret && (
          <Input
            label={t(
              'seed_sources.wizard.secret_slot',
              'Secret slot (keyring ref — never a token)',
            )}
            placeholder="github-read"
            value={authSecretRef}
            onChange={(e) => setAuthSecretRef(e.target.value)}
          />
        )}

        <Input
          label={t('seed_sources.wizard.priority', 'Priority')}
          hint={priorityBand}
          type="number"
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
        />

        {canChooseScope && (
          <fieldset className="space-y-1">
            <legend className="text-sm font-semibold text-gray-900 dark:text-dark-text">
              {t('seed_sources.wizard.scope', 'Scope')}
            </legend>
            <div className="flex gap-2">
              {(['system', 'tenant'] as SeedSourceScope[]).map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={scope === s}
                  onClick={() => {
                    setScope(s);
                    // The scope's priority band flips with the scope — a
                    // tenant overlay defaults into 200+, a system source
                    // into 0–199 (only when the field still holds a
                    // band default, never over a typed value).
                    if (priority === PRIORITY_DEFAULTS.system || priority === PRIORITY_DEFAULTS.tenant) {
                      setPriority(PRIORITY_DEFAULTS[s]);
                    }
                  }}
                  className={`rounded-lg border px-3 py-1.5 text-sm capitalize ${
                    scope === s
                      ? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
                      : 'border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted'
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          </fieldset>
        )}

        {isFile && (
          <div className="space-y-2 rounded-xl border border-dashed border-gray-200 dark:border-dark-border p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-dark-text">
              <CloudUpload className="h-4 w-4" aria-hidden />
              {t('seed_sources.wizard.first_pack', 'First pack (optional now)')}
            </div>
            <p className="text-xs text-gray-500 dark:text-dark-muted">
              {t(
                'seed_sources.wizard.upload_hint',
                'A full pack zip (manifest.json + items/) or a single item-file JSON. The parsed digest lands as a pending version.',
              )}
            </p>
            <input
              type="file"
              accept=".zip,.json"
              className="text-sm"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Input
                label={t('seed_sources.wizard.pack_id', 'Pack id (single-file uploads)')}
                placeholder="my-catalog"
                value={packId}
                onChange={(e) => setPackId(e.target.value)}
              />
              <Input
                label={t('seed_sources.wizard.pack_version', 'Pack version')}
                placeholder="1.0.0"
                value={packVersion}
                onChange={(e) => setPackVersion(e.target.value)}
              />
            </div>
          </div>
        )}
      </div>
    </PanelModal>
  );
}
