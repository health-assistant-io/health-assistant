import React, { useState, useEffect } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { updatePatient } from '../../../services/patientService';
import { SectionHeader, SetupField, SetupInput, SaveBar, type SectionProps } from './_shared';
import { useTranslation } from 'react-i18next';

/**
 * FHIR address line (0..*). Stored as a list on `Patient.address`.
 * Each row: line1, city, postalCode, country, text.
 */
interface AddressRow {
  line?: string;
  city?: string;
  postalCode?: string;
  country?: string;
  text?: string;
}

interface TelecomRow {
  system?: 'phone' | 'email' | 'sms' | 'other';
  value?: string;
  use?: 'home' | 'work' | 'mobile' | 'temp';
}

interface EmergencyContact {
  name?: string;
  relationship?: string;
  phone?: string;
}

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

/**
 * Contacts section: address, telecom (phone/email), emergency contact.
 * All are FHIR 0..* lists (repeatable). Bound to `PUT /patients/:id`.
 */
export const ContactsSection: React.FC<SectionProps> = ({ patient, activeField, onSaved }) => {
  const { t } = useTranslation();
  const [addresses, setAddresses] = useState<AddressRow[]>(() => toAddressRows(patient.address));
  const [telecom, setTelecom] = useState<TelecomRow[]>(() => toTelecomRows(patient.telecom));
  const [emergency, setEmergency] = useState<EmergencyContact>(() => patient.emergency_contact ?? {});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setAddresses(toAddressRows(patient.address));
    setTelecom(toTelecomRows(patient.telecom));
    setEmergency(patient.emergency_contact ?? {});
  }, [patient.id, patient.address, patient.telecom, patient.emergency_contact]);

  const touch = () => setSaved(false);

  const patchAddress = (i: number, field: keyof AddressRow, value: string) => {
    setAddresses((prev) => prev.map((a, idx) => (idx === i ? { ...a, [field]: value } : a)));
    touch();
  };
  const patchTelecom = (i: number, field: keyof TelecomRow, value: string) => {
    setTelecom((prev) => prev.map((t, idx) => (idx === i ? { ...t, [field]: value } : t)));
    touch();
  };

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    try {
      const cleanAddresses = addresses.filter((a) => a.text || a.line || a.city || a.postalCode || a.country);
      const cleanTelecom = telecom.filter((t) => t.value);
      await updatePatient(patient.id, {
        address: cleanAddresses.length ? cleanAddresses : null,
        telecom: cleanTelecom.length ? cleanTelecom : null,
        emergency_contact: emergency.name || emergency.phone ? emergency : null,
      });
      setSaved(true);
      onSaved?.();
    } catch (err) {
      console.error('Failed to save contacts', err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="space-y-8">
      <SectionHeader
        title={t('patient_form.contacts_title')}
        description={t('patient_form.contacts_desc')}
      />

      {/* Address */}
      <div className={activeField === 'address' ? 'ring-2 ring-blue-200 rounded-xl p-3 -m-3' : ''}>
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-sm font-bold text-gray-700 dark:text-dark-text">{t('patient_form.contacts_address')}</h4>
          <button
            type="button"
            onClick={() => { setAddresses((prev) => [...prev, {}]); touch(); }}
            className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 hover:text-blue-700"
          >
            <Plus className="w-3.5 h-3.5" />{t('patient_form.contacts_add_address')}</button>
        </div>
        <div className="space-y-3">
          {addresses.map((a, i) => (
            <div key={i} className="grid grid-cols-1 sm:grid-cols-2 gap-3 p-3 rounded-xl bg-gray-50 dark:bg-dark-bg/50">
              <SetupField label={t('patient_form.contacts_street')}>
                <SetupInput value={a.line ?? ''} onChange={(e) => patchAddress(i, 'line', e.target.value)} placeholder={t('patient_form.contacts_street_placeholder')} />
              </SetupField>
              <SetupField label={t('patient_form.contacts_full')}>
                <SetupInput value={a.text ?? ''} onChange={(e) => patchAddress(i, 'text', e.target.value)} placeholder={t('patient_form.contacts_full_placeholder')} />
              </SetupField>
              <SetupField label={t('patient_form.contacts_city')}>
                <SetupInput value={a.city ?? ''} onChange={(e) => patchAddress(i, 'city', e.target.value)} />
              </SetupField>
              <SetupField label={t('patient_form.contacts_postal_code')}>
                <SetupInput value={a.postalCode ?? ''} onChange={(e) => patchAddress(i, 'postalCode', e.target.value)} />
              </SetupField>
              <SetupField label={t('patient_form.contacts_country')}>
                <SetupInput value={a.country ?? ''} onChange={(e) => patchAddress(i, 'country', e.target.value)} />
              </SetupField>
              {addresses.length > 1 && (
                <div className="flex items-end">
                  <button
                    type="button"
                    onClick={() => { setAddresses((prev) => prev.filter((_, idx) => idx !== i)); touch(); }}
                    className="text-gray-400 hover:text-red-500"
                    aria-label={t('patient_form.contacts_remove_address')}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Telecom */}
      <div className={activeField === 'telecom' ? 'ring-2 ring-blue-200 rounded-xl p-3 -m-3' : ''}>
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-sm font-bold text-gray-700 dark:text-dark-text">{t('patient_form.contacts_phone_email')}</h4>
          <button
            type="button"
            onClick={() => { setTelecom((prev) => [...prev, {}]); touch(); }}
            className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 hover:text-blue-700"
          >
            <Plus className="w-3.5 h-3.5" />{t('patient_form.contacts_add')}</button>
        </div>
        <div className="space-y-3">
          {telecom.map((tel, i) => (
            <div key={i} className="grid grid-cols-1 sm:grid-cols-[140px_1fr_auto] gap-3 items-end">
              <SetupField label={t('patient_form.contacts_type')}>
                <select
                  value={tel.system ?? 'phone'}
                  onChange={(e) => patchTelecom(i, 'system', e.target.value)}
                  className="w-full rounded-xl border border-gray-200 dark:border-dark-border bg-gray-50 dark:bg-dark-bg px-3 py-2.5 text-sm dark:text-dark-text"
                >
                  <option value="phone">{t('patient_form.contacts_phone')}</option>
                  <option value="email">{t('patient_form.contacts_email')}</option>
                  <option value="sms">{t('patient_form.contacts_sms')}</option>
                  <option value="other">{t('patient_form.contacts_other')}</option>
                </select>
              </SetupField>
              <SetupField label={t('patient_form.contacts_value')}>
                <SetupInput value={tel.value ?? ''} onChange={(e) => patchTelecom(i, 'value', e.target.value)} placeholder={t('patient_form.contacts_phone_placeholder')} />
              </SetupField>
              {telecom.length > 1 && (
                <button
                  type="button"
                  onClick={() => { setTelecom((prev) => prev.filter((_, idx) => idx !== i)); touch(); }}
                  className="text-gray-400 hover:text-red-500 mb-2.5"
                  aria-label={t('patient_form.contacts_remove_contact')}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Emergency contact */}
      <div className={activeField === 'emergency_contact' ? 'ring-2 ring-blue-200 rounded-xl p-3 -m-3' : ''}>
        <h4 className="text-sm font-bold text-gray-700 dark:text-dark-text mb-3">{t('patient_form.contacts_emergency')}</h4>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <SetupField label={t('patient_form.contacts_name')}>
            <SetupInput
              value={emergency.name ?? ''}
              onChange={(e) => { setEmergency((p) => ({ ...p, name: e.target.value })); touch(); }}
            />
          </SetupField>
          <SetupField label={t('patient_form.contacts_relationship')}>
            <SetupInput
              value={emergency.relationship ?? ''}
              onChange={(e) => { setEmergency((p) => ({ ...p, relationship: e.target.value })); touch(); }}
              placeholder={t('patient_form.contacts_relationship_placeholder')}
            />
          </SetupField>
          <SetupField label={t('patient_form.contacts_phone')}>
            <SetupInput
              value={emergency.phone ?? ''}
              onChange={(e) => { setEmergency((p) => ({ ...p, phone: e.target.value })); touch(); }}
            />
          </SetupField>
        </div>
      </div>

      <SaveBar onSave={handleSave} saving={saving} saved={saved} />
    </section>
  );
};

export default ContactsSection;
