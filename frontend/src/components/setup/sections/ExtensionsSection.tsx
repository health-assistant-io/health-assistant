import React, { useState, useEffect, useMemo } from 'react';
import { updatePatient } from '../../../services/patientService';
import { getExtensionCatalog } from '../../../services/setupService';
import type { ExtensionCatalog, ExtensionOption } from '../../../types/setup';
import type { PatientExtensions } from '../../../types/patient';
import { SectionHeader, SetupField, SetupInput, SetupSelect, SaveBar, type SectionProps } from './_shared';

function findOption(options: ExtensionOption[] | null | undefined, code?: string): ExtensionOption | undefined {
  if (!options || !code) return undefined;
  return options.find((o) => o.code === code);
}

/**
 * Extensions section: race, ethnicity, preferred language, insurance provider.
 *
 * The inputs are derived from `GET /setup/extension-catalog` so the client
 * never hardcodes extension keys or CDC OMB code lists (the backend owns
 * both). `omb_category` fields render as dropdowns; `code` as a dropdown;
 * `string` as free text.
 *
 * Bound to the `extensions` map on `PUT /patients/:id`. After save, calls
 * `onSaved()` so the parent re-polls the checklist.
 */
export const ExtensionsSection: React.FC<SectionProps> = ({ patient, activeField, onSaved }) => {
  const [catalog, setCatalog] = useState<ExtensionCatalog | null>(null);
  const [draft, setDraft] = useState<PatientExtensions>(() => patient.extensions ?? {});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getExtensionCatalog()
      .then(setCatalog)
      .catch((err) => console.error('Failed to load extension catalog', err));
  }, []);

  useEffect(() => {
    setDraft(patient.extensions ?? {});
  }, [patient.id, patient.extensions]);

  const raceOpts = useMemo(() => catalog?.extensions.find((e) => e.key === 'race')?.options ?? [], [catalog]);
  const ethnicityOpts = useMemo(() => catalog?.extensions.find((e) => e.key === 'ethnicity')?.options ?? [], [catalog]);
  const languageOpts = useMemo(() => catalog?.extensions.find((e) => e.key === 'preferred_language')?.options ?? [], [catalog]);

  const touch = () => setSaved(false);

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    try {
      // Strip empties so the backend validator doesn't reject partial drafts.
      const cleaned: PatientExtensions = {};
      if (draft.race && (draft.race.ombCategory || draft.race.text)) cleaned.race = draft.race;
      if (draft.ethnicity && (draft.ethnicity.ombCategory || draft.ethnicity.text)) cleaned.ethnicity = draft.ethnicity;
      if (draft.preferred_language) cleaned.preferred_language = draft.preferred_language;
      if (draft.insurance_provider) cleaned.insurance_provider = draft.insurance_provider;

      await updatePatient(patient.id, { extensions: Object.keys(cleaned).length ? cleaned : null });
      setSaved(true);
      onSaved?.();
    } catch (err) {
      console.error('Failed to save extensions', err);
    } finally {
      setSaving(false);
    }
  };

  const currentRace = findOption(raceOpts, draft.race?.ombCategory?.code);

  return (
    <section className="space-y-6">
      <SectionHeader
        title="Additional demographics"
        description="Race, ethnicity, preferred language, and insurance — all optional. Stored as FHIR R4 extensions."
        optional
      />

      {!catalog && <p className="text-sm text-gray-400">Loading options…</p>}

      {catalog && (
        <>
          {/* Race */}
          <div className={activeField === 'race_or_ethnicity' ? 'ring-2 ring-blue-200 rounded-xl p-3 -m-3' : ''}>
            <SetupField label="Race">
              <SetupSelect
                value={draft.race?.ombCategory?.code ?? ''}
                onChange={(e) => {
                  const opt = findOption(raceOpts, e.target.value);
                  setDraft((p) => ({
                    ...p,
                    race: opt
                      ? { ombCategory: { code: opt.code, display: opt.display }, text: opt.display }
                      : undefined,
                  }));
                  touch();
                }}
              >
                <option value="">—</option>
                {raceOpts.map((o) => (
                  <option key={o.code} value={o.code}>{o.display}</option>
                ))}
              </SetupSelect>
            </SetupField>
            {currentRace && (
              <SetupField label="Free-text race">
                <SetupInput
                  value={draft.race?.text ?? ''}
                  onChange={(e) => { setDraft((p) => ({ ...p, race: { ...p.race, text: e.target.value } })); touch(); }}
                  placeholder="Optional descriptive text"
                />
              </SetupField>
            )}
          </div>

          {/* Ethnicity */}
          <div className={activeField === 'race_or_ethnicity' ? 'ring-2 ring-blue-200 rounded-xl p-3 -m-3' : ''}>
            <SetupField label="Ethnicity">
              <SetupSelect
                value={draft.ethnicity?.ombCategory?.code ?? ''}
                onChange={(e) => {
                  const opt = findOption(ethnicityOpts, e.target.value);
                  setDraft((p) => ({
                    ...p,
                    ethnicity: opt
                      ? { ombCategory: { code: opt.code, display: opt.display }, text: opt.display }
                      : undefined,
                  }));
                  touch();
                }}
              >
                <option value="">—</option>
                {ethnicityOpts.map((o) => (
                  <option key={o.code} value={o.code}>{o.display}</option>
                ))}
              </SetupSelect>
            </SetupField>
          </div>

          {/* Preferred language */}
          <div className={activeField === 'preferred_language' ? 'ring-2 ring-blue-200 rounded-xl p-3 -m-3' : ''}>
            <SetupField label="Preferred language">
              <SetupSelect
                value={draft.preferred_language ?? ''}
                onChange={(e) => { setDraft((p) => ({ ...p, preferred_language: e.target.value || undefined })); touch(); }}
              >
                <option value="">—</option>
                {languageOpts.map((o) => (
                  <option key={o.code} value={o.code}>{o.display}</option>
                ))}
              </SetupSelect>
            </SetupField>
          </div>

          {/* Insurance */}
          <div className={activeField === 'insurance_provider' ? 'ring-2 ring-blue-200 rounded-xl p-3 -m-3' : ''}>
            <SetupField label="Insurance provider" hint="Free text for now. A proper FHIR Coverage resource is on the roadmap.">
              <SetupInput
                value={draft.insurance_provider ?? ''}
                onChange={(e) => { setDraft((p) => ({ ...p, insurance_provider: e.target.value || undefined })); touch(); }}
                placeholder="e.g. Acme Health Insurance"
              />
            </SetupField>
          </div>
        </>
      )}

      <SaveBar onSave={handleSave} saving={saving} saved={saved} />
    </section>
  );
};

export default ExtensionsSection;
