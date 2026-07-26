import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ExtensionsSection } from './ExtensionsSection';
import type { Patient } from '../../../types/patient';

vi.mock('../../../services/setupService', () => ({
  getExtensionCatalog: vi.fn(),
}));
vi.mock('../../../services/patientService', () => ({
  updatePatient: vi.fn(),
}));

import { getExtensionCatalog } from '../../../services/setupService';
import { updatePatient } from '../../../services/patientService';

const catalogData = {
  entity: 'patient',
  extensions: [
    {
      key: 'race',
      title_i18n_key: 'patient.setup.extension.race',
      value_type: 'omb_category',
      cardinality: '0..1',
      options: [
        { code: '2106-3', display: 'White' },
        { code: '2054-5', display: 'Black or African American' },
      ],
    },
    {
      key: 'ethnicity',
      title_i18n_key: 'patient.setup.extension.ethnicity',
      value_type: 'omb_category',
      cardinality: '0..1',
      options: [
        { code: '2135-2', display: 'Hispanic or Latino' },
        { code: '2186-5', display: 'Not Hispanic or Latino' },
      ],
    },
    {
      key: 'preferred_language',
      title_i18n_key: 'patient.setup.extension.preferred_language',
      value_type: 'code',
      cardinality: '0..1',
      options: [
        { code: 'en', display: 'English' },
        { code: 'el', display: 'Greek' },
      ],
    },
    {
      key: 'insurance_provider',
      title_i18n_key: 'patient.setup.extension.insurance_provider',
      value_type: 'string',
      cardinality: '0..1',
      options: null,
    },
  ],
};

const basePatient: Patient = {
  id: 'p-1',
  tenant_id: 't-1',
  name: { family: 'Doe', given: ['John'] },
  gender: 'male',
  extensions: null,
};

describe('ExtensionsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (getExtensionCatalog as any).mockResolvedValue(catalogData);
    (updatePatient as any).mockResolvedValue({});
  });

  it('loads + renders the catalog options', async () => {
    render(<ExtensionsSection patient={basePatient} />);
    await waitFor(() => expect(getExtensionCatalog).toHaveBeenCalled());
    expect(await screen.findByText('White')).toBeInTheDocument();
    expect(screen.getByText('Black or African American')).toBeInTheDocument();
    expect(screen.getByText('Greek')).toBeInTheDocument();
  });

  it('initialises the draft from the patient extensions', async () => {
    const patient: Patient = {
      ...basePatient,
      extensions: {
        preferred_language: 'el',
        insurance_provider: 'Acme',
      },
    };
    render(<ExtensionsSection patient={patient} />);
    await waitFor(() => expect(getExtensionCatalog).toHaveBeenCalled());
    // The insurance free-text input + the language select both reflect the
    // seeded values once the catalog has loaded and the inputs render.
    await waitFor(() => {
      const insuranceInput = screen.getByPlaceholderText('e.g. Acme Health Insurance') as HTMLInputElement;
      expect(insuranceInput.value).toBe('Acme');
    });
    // Language select: the option with value 'el' (Greek) should be selected.
    const languageSectionSelect = screen.getByText('Greek').closest('select') as HTMLSelectElement;
    expect(languageSectionSelect.value).toBe('el');
  });

  it('writes the extensions patch via updatePatient on save', async () => {
    const onSaved = vi.fn();
    render(<ExtensionsSection patient={basePatient} onSaved={onSaved} />);
    await waitFor(() => expect(getExtensionCatalog).toHaveBeenCalled());

    // Pick race = White
    const raceSelect = screen.getAllByRole('combobox')[0] as HTMLSelectElement;
    fireEvent.change(raceSelect, { target: { value: '2106-3' } });

    // Set insurance
    const insuranceInput = screen.getByPlaceholderText('e.g. Acme Health Insurance') as HTMLInputElement;
    fireEvent.change(insuranceInput, { target: { value: 'Acme Health' } });

    // Save
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(updatePatient).toHaveBeenCalled());
    const [id, payload] = (updatePatient as any).mock.calls[0];
    expect(id).toBe('p-1');
    expect(payload.extensions).toMatchObject({
      race: { ombCategory: { code: '2106-3', display: 'White' }, text: 'White' },
      insurance_provider: 'Acme Health',
    });
    expect(onSaved).toHaveBeenCalled();
  });

  it('sends null when nothing is set (clears extensions)', async () => {
    render(<ExtensionsSection patient={basePatient} />);
    await waitFor(() => expect(getExtensionCatalog).toHaveBeenCalled());

    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(updatePatient).toHaveBeenCalled());
    const [, payload] = (updatePatient as any).mock.calls[0];
    expect(payload.extensions).toBeNull();
  });
});
