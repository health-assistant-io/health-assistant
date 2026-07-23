import React, { useCallback, useEffect, useState } from 'react';
import { toast } from 'react-toastify';
import {
  KeyRound,
  Plus,
  RefreshCw,
  Trash2,
  Power,
  PowerOff,
  ShieldAlert,
  Copy,
  AlertCircle,
} from 'lucide-react';

import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';
import { FormModal } from '../../components/ui/FormModal';
import { Modal } from '../../components/ui/Modal';
import { CopyButton } from '../../components/ui/CopyButton';
import { oauthService } from '../../services/oauthService';
import type { OAuthClient } from '../../types/oauth';
import { useUIStore } from '../../store/slices/uiSlice';

/** Scope presets offered as one-click additions in the editor. */
const SCOPE_PRESETS: { label: string; scope: string }[] = [
  { label: 'system/*.read', scope: 'system/*.read' },
  { label: 'system/*.write', scope: 'system/*.write' },
  { label: 'system/*.* (full)', scope: 'system/*.*' },
  { label: 'patient/Observation.read', scope: 'patient/Observation.read' },
  { label: 'patient/Observation.write', scope: 'patient/Observation.write' },
];

/** Parse a free-form scope string (space/newline/comma separated) into a list. */
function parseScopes(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function hasPatientScope(scopes: string[]): boolean {
  return scopes.some((s) => s.startsWith('patient/'));
}

/** Parse + validate the client_id (ci_…) shown in the list. */
const ClientIdChip: React.FC<{ value: string }> = ({ value }) => (
  <code className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-gray-100 dark:bg-dark-bg text-gray-700 dark:text-dark-muted text-xs font-mono">
    {value}
    <CopyButton value={value} label="Copy client id" size={12} />
  </code>
);

const OAuthClients: React.FC = () => {
  const [clients, setClients] = useState<OAuthClient[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  // Create / edit modal state.
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<OAuthClient | null>(null);
  const [name, setName] = useState('');
  const [scopesRaw, setScopesRaw] = useState('');
  const [boundPatient, setBoundPatient] = useState('');

  // Plaintext secret reveal (shown once after create / rotate).
  const [revealed, setRevealed] = useState<{ clientId: string; secret: string; title: string } | null>(null);

  const showConfirmation = useUIStore((s) => s.showConfirmation);

  const fetchClients = useCallback(async () => {
    try {
      setLoading(true);
      setClients(await oauthService.list());
    } catch (e) {
      console.error('Failed to load OAuth clients', e);
      toast.error('Failed to load OAuth clients');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchClients();
  }, [fetchClients]);

  const openCreate = () => {
    setEditing(null);
    setName('');
    setScopesRaw('system/*.read');
    setBoundPatient('');
    setFormOpen(true);
  };

  const openEdit = (c: OAuthClient) => {
    setEditing(c);
    setName(c.display_name);
    setScopesRaw(c.scopes.join(' '));
    setBoundPatient(c.bound_patient_id ?? '');
    setFormOpen(true);
  };

  const closeForm = () => {
    setFormOpen(false);
    setEditing(null);
  };

  const submitForm = async () => {
    const scopes = parseScopes(scopesRaw);
    if (!name.trim()) {
      toast.error('A display name is required.');
      return;
    }
    if (scopes.length === 0) {
      toast.error('At least one SMART scope is required.');
      return;
    }
    if (hasPatientScope(scopes) && !boundPatient.trim()) {
      toast.error('A bound patient id is required for patient/ scopes.');
      return;
    }
    setBusy(true);
    try {
      if (editing) {
        await oauthService.update(editing.id, {
          display_name: name.trim(),
          scopes,
          bound_patient_id: hasPatientScope(scopes) ? boundPatient.trim() : null,
        });
        toast.success('Client updated.');
        closeForm();
        fetchClients();
      } else {
        const created = await oauthService.create({
          display_name: name.trim(),
          scopes,
          bound_patient_id: hasPatientScope(scopes) ? boundPatient.trim() : undefined,
        });
        toast.success('Client created.');
        closeForm();
        fetchClients();
        // Reveal the one-time plaintext secret.
        setRevealed({ clientId: created.client_id, secret: created.client_secret, title: 'New client secret' });
      }
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Failed to save client.');
    } finally {
      setBusy(false);
    }
  };

  const handleRotate = (c: OAuthClient) => {
    showConfirmation({
      title: 'Rotate client secret?',
      message: `A new secret will be issued for "${c.display_name}". The old secret stops working immediately. The new secret is shown only once.`,
      confirmLabel: 'Rotate',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          const r = await oauthService.rotateSecret(c.id);
          toast.success('Secret rotated.');
          setRevealed({ clientId: c.client_id, secret: r.client_secret, title: 'Rotated client secret' });
        } catch {
          toast.error('Failed to rotate secret.');
        }
      },
    });
  };

  const handleToggle = async (c: OAuthClient) => {
    try {
      await oauthService.update(c.id, { is_active: !c.is_active });
      toast.success(`Client ${c.is_active ? 'disabled' : 'enabled'}.`);
      fetchClients();
    } catch {
      toast.error('Failed to update client.');
    }
  };

  const handleDelete = (c: OAuthClient) => {
    showConfirmation({
      title: 'Delete OAuth client?',
      message: `"${c.display_name}" will be permanently deleted. Tokens already issued remain valid until they expire — disable first to cut access sooner.`,
      confirmLabel: 'Delete',
      confirmVariant: 'danger',
      onConfirm: async () => {
        try {
          await oauthService.remove(c.id);
          toast.success('Client deleted.');
          fetchClients();
        } catch {
          toast.error('Failed to delete client.');
        }
      },
    });
  };

  const scopes = parseScopes(scopesRaw);
  const showBoundPatient = hasPatientScope(scopes);

  return (
    <div className="space-y-6">
      <PageHeader
        title="API Clients"
        subtitle="OAuth2 clients for external systems to access the FHIR R4 facade"
        icon={<KeyRound className="w-8 h-8 text-blue-500" />}
        actions={
          <button
            onClick={openCreate}
            className="inline-flex items-center px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-xl shadow-sm hover:bg-blue-700 transition-colors"
          >
            <Plus className="w-4 h-4 mr-2" />
            New Client
          </button>
        }
      />

      <div className="bg-white dark:bg-dark-surface shadow rounded-2xl overflow-hidden border border-gray-100 dark:border-dark-border">
        {loading ? (
          <LoadingState variant="section" message="Loading API clients…" />
        ) : clients.length === 0 ? (
          <div className="p-10 text-center text-gray-500 dark:text-dark-muted">
            <KeyRound className="w-10 h-10 mx-auto mb-3 opacity-40" />
            No OAuth clients registered yet. Create one to let an external system read or write FHIR resources.
          </div>
        ) : (
          <ul className="divide-y divide-gray-200 dark:divide-dark-border">
            {clients.map((c) => (
              <li key={c.id} className="px-6 py-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-3 flex-wrap">
                      <h4 className="text-lg font-bold text-gray-900 dark:text-dark-text truncate">
                        {c.display_name}
                      </h4>
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          c.is_active
                            ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
                            : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400'
                        }`}
                      >
                        {c.is_active ? 'Active' : 'Disabled'}
                      </span>
                    </div>
                    <div className="mt-2">
                      <ClientIdChip value={c.client_id} />
                    </div>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {c.scopes.length === 0 ? (
                        <span className="text-xs text-gray-400 dark:text-dark-muted">No scopes</span>
                      ) : (
                        c.scopes.map((s) => (
                          <span
                            key={s}
                            className="inline-flex items-center px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 text-xs font-mono"
                          >
                            {s}
                          </span>
                        ))
                      )}
                    </div>
                    {c.bound_patient_id && (
                      <p className="mt-2 text-xs text-gray-500 dark:text-dark-muted">
                        Bound patient: <code>{c.bound_patient_id}</code>
                      </p>
                    )}
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0">
                    <IconAction title="Edit" onClick={() => openEdit(c)} icon={<ShieldAlert className="w-4 h-4" />} />
                    <IconAction title="Rotate secret" onClick={() => handleRotate(c)} icon={<RefreshCw className="w-4 h-4" />} />
                    <IconAction
                      title={c.is_active ? 'Disable' : 'Enable'}
                      onClick={() => handleToggle(c)}
                      icon={c.is_active ? <PowerOff className="w-4 h-4" /> : <Power className="w-4 h-4" />}
                    />
                    <IconAction title="Delete" onClick={() => handleDelete(c)} danger icon={<Trash2 className="w-4 h-4" />} />
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Create / edit form */}
      <FormModal
        isOpen={formOpen}
        onClose={closeForm}
        title={editing ? 'Edit API Client' : 'New API Client'}
        icon={<KeyRound className="w-5 h-5 text-blue-500" />}
        onSubmit={submitForm}
        submitting={busy}
        submitLabel={editing ? 'Save' : 'Create'}
      >
        <div className="space-y-4">
          <Field label="Display name">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Hospital Lab Sync"
              className="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-bg text-sm"
            />
          </Field>

          <Field
            label="SMART scopes"
            hint="Space-separated. Syntax: context/Resource.permission — e.g. system/Observation.read, system/*.write, patient/Condition.read"
          >
            <textarea
              value={scopesRaw}
              onChange={(e) => setScopesRaw(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-bg text-sm font-mono"
              placeholder="system/Observation.read system/Patient.read"
            />
            <div className="flex flex-wrap gap-1.5 mt-2">
              {SCOPE_PRESETS.map((p) => (
                <button
                  key={p.scope}
                  type="button"
                  onClick={() =>
                    setScopesRaw((prev) => (prev.includes(p.scope) ? prev : `${prev} ${p.scope}`.trim()))
                  }
                  className="px-2 py-0.5 rounded-md bg-gray-100 dark:bg-dark-bg text-gray-700 dark:text-dark-muted text-xs font-mono hover:bg-gray-200 dark:hover:bg-dark-border"
                >
                  + {p.label}
                </button>
              ))}
            </div>
          </Field>

          {showBoundPatient && (
            <Field
              label="Bound patient id"
              hint="Required for patient/ scopes — the client is restricted to this one patient."
            >
              <input
                value={boundPatient}
                onChange={(e) => setBoundPatient(e.target.value)}
                placeholder="Patient UUID"
                className="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-bg text-sm font-mono"
              />
            </Field>
          )}

          {!editing && (
            <div className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 rounded-lg p-3">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>
                The <code>client_secret</code> is shown <strong>only once</strong> after creation. Store it
                securely — you will need it to call <code>POST /api/v1/oauth/token</code>.
              </span>
            </div>
          )}
        </div>
      </FormModal>

      {/* One-time secret reveal */}
      <Modal isOpen={revealed !== null} onClose={() => setRevealed(null)} title={revealed?.title ?? 'Client secret'}>
        {revealed && (
          <div className="space-y-4">
            <div className="flex items-start gap-2 text-sm text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 rounded-lg p-3">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>Copy this secret now. For security, it cannot be retrieved again.</span>
            </div>
            <div>
              <p className="text-xs text-gray-500 dark:text-dark-muted mb-1">client_id</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 rounded-lg bg-gray-100 dark:bg-dark-bg text-sm font-mono break-all">
                  {revealed.clientId}
                </code>
                <CopyButton value={revealed.clientId} label="Copy client id" />
              </div>
            </div>
            <div>
              <p className="text-xs text-gray-500 dark:text-dark-muted mb-1">client_secret</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 rounded-lg bg-gray-100 dark:bg-dark-bg text-sm font-mono break-all">
                  {revealed.secret}
                </code>
                <CopyButton value={revealed.secret} label="Copy client secret" />
              </div>
            </div>
            <div className="flex justify-end pt-2">
              <button
                onClick={() => setRevealed(null)}
                className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-xl hover:bg-blue-700"
              >
                <Copy className="w-4 h-4" />
                I&apos;ve saved it
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};

const IconAction: React.FC<{
  title: string;
  onClick: () => void;
  icon: React.ReactNode;
  danger?: boolean;
}> = ({ title, onClick, icon, danger }) => (
  <button
    title={title}
    onClick={onClick}
    className={`p-2 rounded-lg transition-colors ${
      danger
        ? 'text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20'
        : 'text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20'
    }`}
  >
    {icon}
  </button>
);

const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => (
  <div>
    <label className="block text-sm font-medium text-gray-700 dark:text-dark-text mb-1">{label}</label>
    {children}
    {hint && <p className="mt-1 text-xs text-gray-500 dark:text-dark-muted">{hint}</p>}
  </div>
);

export default OAuthClients;
