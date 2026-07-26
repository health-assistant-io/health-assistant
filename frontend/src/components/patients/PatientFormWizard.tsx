import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Check, ChevronLeft, ChevronRight, User, Phone, Globe } from 'lucide-react';
import { FormModal } from '../ui/FormModal';
import { DatePicker } from '../ui/DatePicker';
import { createPatient, updatePatient } from '../../services/patientService';
import { getExtensionCatalog } from '../../services/setupService';
import { useAuthStore } from '../../store/slices/authSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import type { Patient, PatientExtensions } from '../../types/patient';
import type { ExtensionCatalog, ExtensionOption } from '../../types/setup';
import { SetupField, SetupInput, SetupSelect } from '../setup/sections/_shared';

const GENDERS = ['male', 'female', 'other', 'unknown'] as const;

interface AddressRow {
  line?: string; city?: string; postalCode?: string; country?: string; text?: string;
}
interface TelecomRow {
  system?: 'phone' | 'email' | 'sms' | 'other'; value?: string; use?: 'home' | 'work' | 'mobile' | 'temp';
}
interface EmergencyContact { name?: string; relationship?: string; phone?: string; }

interface DraftState {
  firstName: string; lastName: string; gender: string;
  birthDate: string; mrn: string;
  addresses: AddressRow[];
  telecom: TelecomRow[];
  emergency: EmergencyContact;
  extensions: PatientExtensions;
}

const EMPTY_DRAFT: DraftState = {
  firstName: '', lastName: '', gender: 'unknown',
  birthDate: '', mrn: '',
  addresses: [{}], telecom: [{}], emergency: {},
  extensions: {},
};

const STEPS = [
  { id: 'basic', icon: User },
  { id: 'contacts', icon: Phone },
  { id: 'demographics', icon: Globe },
] as const;

interface PatientFormWizardProps {
  isOpen: boolean;
  onClose: () => void;
  /** Existing patient for edit mode; null/undefined for create. */
  patient?: Patient | null;
  /** Called after a successful save with the updated/created patient. */
  onSaved?: (patient: Patient) => void;
}

/**
 * Multi-step patient create/edit form — replaces the old 5-field modal.
 *
 * Three category pages:
 * 1. **Basic info** — name, sex, date of birth, MRN (required minimum to create).
 * 2. **Contact info** — FHIR `0..*` address + telecom + emergency contact.
 * 3. **Additional demographics** — race / ethnicity / preferred language / insurance
 *    (driven by `GET /setup/extension-catalog`, same as the setup wizard).
 *
 * **Create mode** (no `patient`): step 1 creates the patient via `createPatient`;
 * steps 2–3 are optional and persist via `updatePatient` on the new ID. The user
 * can skip to "Finish" from any step.
 *
 * **Edit mode** (`patient` provided): all steps show existing data; "Save" on
 * any step persists all accumulated changes via one `updatePatient` call.
 *
 * The draft state is unified — all fields live in one object, so navigating
 * between steps never loses data. No per-section save buttons; the wizard owns
 * the lifecycle.
 */
export const PatientFormWizard: React.FC<PatientFormWizardProps> = ({
  isOpen, onClose, patient, onSaved,
}) => {
  const { t } = useTranslation();
  const { user } = useAuthStore();
  const setCurrentPatient = usePatientStore((s) => s.setCurrentPatient);

  const [draft, setDraft] = useState<DraftState>(EMPTY_DRAFT);
  const [activeStep, setActiveStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<ExtensionCatalog | null>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);

  // Load extension catalog once.
  useEffect(() => {
    getExtensionCatalog().then(setCatalog).catch(() => {});
  }, []);

  // Hydrate draft from existing patient (edit mode) or reset (create mode).
  useEffect(() => {
    if (!isOpen) return;
    setCreatedId(null);
    setActiveStep(0);
    setError(null);
    if (patient) {
      const name = patient.name ?? {};
      const given = Array.isArray(name.given) ? name.given : [];
      setDraft({
        firstName: given[0] ?? '',
        lastName: name.family ?? '',
        gender: (patient.gender ?? 'unknown').toLowerCase(),
        birthDate: patient.birth_date ?? '',
        mrn: patient.mrn ?? '',
        addresses: toAddressRows(patient.address),
        telecom: toTelecomRows(patient.telecom),
        emergency: patient.emergency_contact ?? {},
        extensions: patient.extensions ?? {},
      });
    } else {
      setDraft(EMPTY_DRAFT);
    }
  }, [isOpen, patient]);

  const isCreate = !patient && !createdId;
  const canProceedStep0 = draft.firstName.trim() && draft.lastName.trim();

  // --- Save logic ---
  const buildPayload = useCallback(() => ({
    name: { given: [draft.firstName.trim()], family: draft.lastName.trim() },
    gender: draft.gender,
    birth_date: draft.birthDate || null,
    mrn: draft.mrn || null,
    address: draft.addresses.filter((a) => a.line || a.city || a.postalCode || a.country || a.text) || null,
    telecom: draft.telecom.filter((tc) => tc.value) || null,
    emergency_contact: (draft.emergency.name || draft.emergency.phone) ? draft.emergency : null,
    extensions: Object.keys(draft.extensions).length ? cleanExtensions(draft.extensions) : null,
  } as any), [draft]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      let tenantId = user?.tenant_id;
      if (!tenantId) {
        const token = localStorage.getItem('accessToken');
        if (token) tenantId = JSON.parse(atob(token.split('.')[1])).tenant_id;
      }

      if (isCreate) {
        // Create with basic fields first, then update with the rest.
        const created = await createPatient({
          name: { given: [draft.firstName.trim()], family: draft.lastName.trim() },
          gender: draft.gender,
          birth_date: draft.birthDate || undefined,
          mrn: draft.mrn || undefined,
        } as any, tenantId);
        setCreatedId(created.id);

        // Update with contacts + extensions in a second call.
        const extra = buildPayload();
        const updated = await updatePatient(created.id, extra);
        if (!usePatientStore.getState().currentPatient) setCurrentPatient(updated);
        onSaved?.(updated);
      } else {
        const id = patient?.id ?? createdId!;
        const updated = await updatePatient(id, buildPayload());
        onSaved?.(updated);
      }
      onClose();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : t('common.error', 'Failed to save.'));
    } finally {
      setSaving(false);
    }
  }, [isCreate, draft, patient, createdId, user, t, buildPayload, onClose, onSaved, setCurrentPatient]);

  // --- Step navigation ---
  const goNext = () => setActiveStep((s) => Math.min(s + 1, STEPS.length - 1));
  const goPrev = () => setActiveStep((s) => Math.max(s - 1, 0));

  const patch = (p: Partial<DraftState>) => setDraft((prev) => ({ ...prev, ...p }));

  return (
    <FormModal
      isOpen={isOpen}
      onClose={onClose}
      title={patient ? t('patients.edit_profile') : t('patients.add_new')}
      icon={<div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-lg"><User className="w-5 h-5 text-blue-600" /></div>}
      hideFooter
      size="lg"
      bodyClassName="p-0"
    >
      {/* Step indicator */}
      <div className="flex items-center gap-1 px-6 pt-5 pb-3 border-b border-gray-100 dark:border-dark-border">
        {STEPS.map((step, idx) => {
          const Icon = step.icon;
          const isActive = idx === activeStep;
          const isPast = idx < activeStep;
          return (
            <React.Fragment key={step.id}>
              {idx > 0 && <div className={`flex-1 h-0.5 rounded-full ${isPast ? 'bg-blue-500' : 'bg-gray-200 dark:bg-dark-border'}`} />}
              <button
                type="button"
                onClick={() => idx === 0 || canProceedStep0 || patient ? setActiveStep(idx) : null}
                disabled={idx > 0 && !canProceedStep0 && !patient}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-colors ${
                  isActive ? 'bg-blue-600 text-white' : isPast ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300' : 'bg-gray-100 text-gray-400 dark:bg-dark-border'
                } ${idx > 0 && !canProceedStep0 && !patient ? 'opacity-50 cursor-not-allowed' : ''}`}
              >
                {isPast ? <Check className="w-3.5 h-3.5" /> : <Icon className="w-3.5 h-3.5" />}
                <span className="hidden sm:inline">{t(`patient_form.steps.${step.id}`, step.id)}</span>
              </button>
            </React.Fragment>
          );
        })}
      </div>

      {/* Error */}
      {error && (
        <div className="mx-6 mt-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-100 dark:border-red-800/40 text-red-600 dark:text-red-400 text-sm rounded-xl">
          {error}
        </div>
      )}

      {/* Step content */}
      <div className="px-6 py-5 max-h-[55vh] overflow-y-auto">
        {activeStep === 0 && (
          <BasicInfoStep draft={draft} patch={patch} t={t} />
        )}
        {activeStep === 1 && (
          <ContactInfoStep draft={draft} patch={patch} t={t} />
        )}
        {activeStep === 2 && (
          <DemographicsStep draft={draft} patch={patch} t={t} catalog={catalog} />
        )}
      </div>

      {/* Footer: step navigation */}
      <div className="flex items-center justify-between px-6 py-4 border-t border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-surface/50">
        <button
          type="button"
          onClick={activeStep === 0 ? onClose : goPrev}
          className="px-4 py-2 text-sm font-semibold text-gray-500 hover:text-gray-700 dark:text-dark-muted rounded-xl hover:bg-gray-100 dark:hover:bg-dark-border/50"
        >
          {activeStep === 0 ? t('common.cancel') : (<><ChevronLeft className="w-4 h-4 inline mr-1" />{t('setup.previous')}</>)}
        </button>
        <div className="flex items-center gap-2">
          {activeStep < STEPS.length - 1 && (
            <button
              type="button"
              onClick={goNext}
              disabled={activeStep === 0 && !canProceedStep0}
              className="px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1"
            >
              {t('setup.next')} <ChevronRight className="w-4 h-4" />
            </button>
          )}
          {/* Skip (create mode — finish early) */}
          {isCreate && activeStep < STEPS.length - 1 && (
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || (activeStep === 0 && !canProceedStep0)}
              className="px-4 py-2 text-sm font-semibold text-gray-500 hover:text-gray-700 dark:text-dark-muted rounded-xl hover:bg-gray-100 dark:hover:bg-dark-border/50"
            >
              {t('patient_form.skip_finish', 'Skip & finish')}
            </button>
          )}
          {activeStep === STEPS.length - 1 && (
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || (activeStep === 0 && !canProceedStep0)}
              className="px-4 py-2 bg-green-600 text-white rounded-xl text-sm font-semibold hover:bg-green-700 disabled:opacity-50 inline-flex items-center gap-1.5"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              {patient ? t('common.save') : t('patients.create_patient')}
            </button>
          )}
        </div>
      </div>
    </FormModal>
  );
};

// ---------------------------------------------------------------------------
// Step components (inline — no per-step save; the wizard owns the draft)
// ---------------------------------------------------------------------------

const BasicInfoStep: React.FC<{ draft: DraftState; patch: (p: Partial<DraftState>) => void; t: any }> = ({ draft, patch, t }) => (
  <div className="space-y-4">
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <SetupField label={t('patients.first_name', 'First name')} htmlFor="pfw-first">
        <SetupInput id="pfw-first" value={draft.firstName} onChange={(e) => patch({ firstName: e.target.value })} placeholder="John" />
      </SetupField>
      <SetupField label={t('patients.last_name', 'Last name')} htmlFor="pfw-last">
        <SetupInput id="pfw-last" value={draft.lastName} onChange={(e) => patch({ lastName: e.target.value })} placeholder="Doe" />
      </SetupField>
    </div>
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <SetupField label={t('patients.dob', 'Date of birth')}>
        <DatePicker value={draft.birthDate} onChange={(date) => patch({ birthDate: date })} allowClear />
      </SetupField>
      <SetupField label={t('patients.mrn', 'MRN')}>
        <SetupInput value={draft.mrn} onChange={(e) => patch({ mrn: e.target.value })} placeholder="PAT-123456" />
      </SetupField>
    </div>
    <SetupField label={t('patients.gender', 'Sex')}>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {GENDERS.map((g) => (
          <button key={g} type="button" onClick={() => patch({ gender: g })}
            className={`px-3 py-2 rounded-xl text-sm font-medium border transition-all capitalize ${
              draft.gender === g ? 'bg-blue-600 border-blue-600 text-white' : 'bg-white dark:bg-dark-bg border-gray-200 dark:border-dark-border text-gray-600 dark:text-dark-muted hover:bg-gray-50'
            }`}>{g}</button>
        ))}
      </div>
    </SetupField>
  </div>
);

const ContactInfoStep: React.FC<{ draft: DraftState; patch: (p: Partial<DraftState>) => void; t: any }> = ({ draft, patch, t }) => {
  const patchAddr = (i: number, field: keyof AddressRow, val: string) =>
    patch({ addresses: draft.addresses.map((a, idx) => idx === i ? { ...a, [field]: val } : a) });
  const patchTel = (i: number, field: keyof TelecomRow, val: string) =>
    patch({ telecom: draft.telecom.map((tc, idx) => idx === i ? { ...tc, [field]: val } : tc) });
  const patchEmerg = (field: keyof EmergencyContact, val: string) =>
    patch({ emergency: { ...draft.emergency, [field]: val } });

  return (
    <div className="space-y-6">
      {/* Address */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-xs font-bold uppercase tracking-wide text-gray-400">{t('patient_form.address', 'Address')}</h4>
          <button type="button" onClick={() => patch({ addresses: [...draft.addresses, {}] })} className="text-xs font-semibold text-blue-600">+ {t('patient_form.add', 'Add')}</button>
        </div>
        {draft.addresses.map((a, i) => (
          <div key={i} className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-2 p-3 rounded-xl bg-gray-50 dark:bg-dark-bg/50">
            <SetupField label="Street"><SetupInput value={a.line ?? ''} onChange={(e) => patchAddr(i, 'line', e.target.value)} placeholder="123 Main St" /></SetupField>
            <SetupField label="City"><SetupInput value={a.city ?? ''} onChange={(e) => patchAddr(i, 'city', e.target.value)} /></SetupField>
            <SetupField label="Postal code"><SetupInput value={a.postalCode ?? ''} onChange={(e) => patchAddr(i, 'postalCode', e.target.value)} /></SetupField>
            <SetupField label="Country"><SetupInput value={a.country ?? ''} onChange={(e) => patchAddr(i, 'country', e.target.value)} /></SetupField>
            {draft.addresses.length > 1 && (
              <button type="button" onClick={() => patch({ addresses: draft.addresses.filter((_, idx) => idx !== i) })} className="text-xs text-red-400 hover:text-red-600 self-end">Remove</button>
            )}
          </div>
        ))}
      </div>

      {/* Telecom */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-xs font-bold uppercase tracking-wide text-gray-400">{t('patient_form.phone_email', 'Phone & email')}</h4>
          <button type="button" onClick={() => patch({ telecom: [...draft.telecom, {}] })} className="text-xs font-semibold text-blue-600">+ {t('patient_form.add', 'Add')}</button>
        </div>
        {draft.telecom.map((tc, i) => (
          <div key={i} className="grid grid-cols-[120px_1fr_auto] gap-2 items-end mb-2">
            <SetupField label="Type">
              <select value={tc.system ?? 'phone'} onChange={(e) => patchTel(i, 'system', e.target.value)}
                className="w-full rounded-xl border border-gray-200 dark:border-dark-border bg-gray-50 dark:bg-dark-bg px-3 py-2.5 text-sm dark:text-dark-text">
                <option value="phone">Phone</option><option value="email">Email</option><option value="sms">SMS</option>
              </select>
            </SetupField>
            <SetupField label="Value"><SetupInput value={tc.value ?? ''} onChange={(e) => patchTel(i, 'value', e.target.value)} placeholder="+30 ..." /></SetupField>
            {draft.telecom.length > 1 && (
              <button type="button" onClick={() => patch({ telecom: draft.telecom.filter((_, idx) => idx !== i) })} className="text-xs text-red-400 hover:text-red-600 pb-2.5">✕</button>
            )}
          </div>
        ))}
      </div>

      {/* Emergency contact */}
      <div>
        <h4 className="text-xs font-bold uppercase tracking-wide text-gray-400 mb-2">{t('patient_form.emergency_contact', 'Emergency contact')}</h4>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <SetupField label="Name"><SetupInput value={draft.emergency.name ?? ''} onChange={(e) => patchEmerg('name', e.target.value)} /></SetupField>
          <SetupField label="Relationship"><SetupInput value={draft.emergency.relationship ?? ''} onChange={(e) => patchEmerg('relationship', e.target.value)} placeholder="Spouse" /></SetupField>
          <SetupField label="Phone"><SetupInput value={draft.emergency.phone ?? ''} onChange={(e) => patchEmerg('phone', e.target.value)} /></SetupField>
        </div>
      </div>
    </div>
  );
};

const DemographicsStep: React.FC<{ draft: DraftState; patch: (p: Partial<DraftState>) => void; t: any; catalog: ExtensionCatalog | null }> = ({ draft, patch, t, catalog }) => {
  const raceOpts = useMemo(() => catalog?.extensions.find((e) => e.key === 'race')?.options ?? [], [catalog]);
  const ethnicityOpts = useMemo(() => catalog?.extensions.find((e) => e.key === 'ethnicity')?.options ?? [], [catalog]);
  const languageOpts = useMemo(() => catalog?.extensions.find((e) => e.key === 'preferred_language')?.options ?? [], [catalog]);
  const findOpt = (opts: ExtensionOption[], code?: string) => opts.find((o) => o.code === code);

  return (
    <div className="space-y-4">
      <SetupField label={t('patient_form.race', 'Race')}>
        <SetupSelect value={draft.extensions.race?.ombCategory?.code ?? ''} onChange={(e) => {
          const opt = findOpt(raceOpts, e.target.value);
          patch({ extensions: { ...draft.extensions, race: opt ? { ombCategory: { code: opt.code, display: opt.display }, text: opt.display } : undefined } });
        }}>
          <option value="">—</option>
          {raceOpts.map((o) => <option key={o.code} value={o.code}>{o.display}</option>)}
        </SetupSelect>
      </SetupField>

      <SetupField label={t('patient_form.ethnicity', 'Ethnicity')}>
        <SetupSelect value={draft.extensions.ethnicity?.ombCategory?.code ?? ''} onChange={(e) => {
          const opt = findOpt(ethnicityOpts, e.target.value);
          patch({ extensions: { ...draft.extensions, ethnicity: opt ? { ombCategory: { code: opt.code, display: opt.display }, text: opt.display } : undefined } });
        }}>
          <option value="">—</option>
          {ethnicityOpts.map((o) => <option key={o.code} value={o.code}>{o.display}</option>)}
        </SetupSelect>
      </SetupField>

      <SetupField label={t('patient_form.preferred_language', 'Preferred language')}>
        <SetupSelect value={draft.extensions.preferred_language ?? ''} onChange={(e) => patch({ extensions: { ...draft.extensions, preferred_language: e.target.value || undefined } })}>
          <option value="">—</option>
          {languageOpts.map((o) => <option key={o.code} value={o.code}>{o.display}</option>)}
        </SetupSelect>
      </SetupField>

      <SetupField label={t('patient_form.insurance', 'Insurance provider')} hint={t('patient_form.insurance_hint', 'Free text for now; a proper FHIR Coverage resource is roadmap.')}>
        <SetupInput value={draft.extensions.insurance_provider ?? ''} onChange={(e) => patch({ extensions: { ...draft.extensions, insurance_provider: e.target.value || undefined } })} placeholder="Acme Health" />
      </SetupField>
    </div>
  );
};

// --- helpers ---
function toAddressRows(raw: any): AddressRow[] {
  if (!raw) return [{}];
  const arr = Array.isArray(raw) ? raw : [raw];
  return arr.length ? arr : [{}];
}
function toTelecomRows(raw: any): TelecomRow[] {
  if (!raw) return [{}];
  const arr = Array.isArray(raw) ? raw : [raw];
  return arr.length ? arr : [{}];
}
function cleanExtensions(ext: PatientExtensions): PatientExtensions {
  const out: PatientExtensions = {};
  if (ext.race && (ext.race.ombCategory || ext.race.text)) out.race = ext.race;
  if (ext.ethnicity && (ext.ethnicity.ombCategory || ext.ethnicity.text)) out.ethnicity = ext.ethnicity;
  if (ext.preferred_language) out.preferred_language = ext.preferred_language;
  if (ext.insurance_provider) out.insurance_provider = ext.insurance_provider;
  return out;
}

export default PatientFormWizard;
