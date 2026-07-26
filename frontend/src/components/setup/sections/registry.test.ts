import { describe, it, expect } from 'vitest';
import { resolveSection, INLINE_SECTIONS } from './registry';
import type { SetupStep } from '../../../types/setup';
import { DemographicsSection } from './DemographicsSection';
import { ContactsSection } from './ContactsSection';
import { ExtensionsSection } from './ExtensionsSection';

function step(id: string): SetupStep {
  return {
    id,
    title_i18n_key: `setup.steps.${id}`,
    kind: 'inline_form',
    completed: false,
    optional: false,
  };
}

describe('INLINE_SECTIONS registry', () => {
  it('maps birth_date to DemographicsSection', () => {
    const entry = resolveSection(step('patient.birth_date'));
    expect(entry?.Component).toBe(DemographicsSection);
    expect(entry?.field).toBeUndefined();
  });

  it('maps the three contact step ids to ContactsSection with distinct fields', () => {
    expect(resolveSection(step('patient.address'))).toMatchObject({ Component: ContactsSection, field: 'address' });
    expect(resolveSection(step('patient.telecom'))).toMatchObject({ Component: ContactsSection, field: 'telecom' });
    expect(resolveSection(step('patient.emergency_contact'))).toMatchObject({
      Component: ContactsSection,
      field: 'emergency_contact',
    });
  });

  it('maps the three extension step ids to ExtensionsSection', () => {
    expect(resolveSection(step('patient.race_or_ethnicity'))).toMatchObject({
      Component: ExtensionsSection,
      field: 'race_or_ethnicity',
    });
    expect(resolveSection(step('patient.preferred_language'))).toMatchObject({
      Component: ExtensionsSection,
      field: 'preferred_language',
    });
    expect(resolveSection(step('patient.insurance_provider'))).toMatchObject({
      Component: ExtensionsSection,
      field: 'insurance_provider',
    });
  });

  it('returns null for an unregistered step id', () => {
    expect(resolveSection(step('patient.unknown_future_step'))).toBeNull();
  });

  it('covers every patient inline_form step id the backend ships', () => {
    const knownPatientStepIds = [
      'patient.birth_date',
      'patient.address',
      'patient.telecom',
      'patient.emergency_contact',
      'patient.race_or_ethnicity',
      'patient.preferred_language',
      'patient.insurance_provider',
    ];
    for (const id of knownPatientStepIds) {
      expect(INLINE_SECTIONS[id]).toBeDefined();
    }
  });
});
