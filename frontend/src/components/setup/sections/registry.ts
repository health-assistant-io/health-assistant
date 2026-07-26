import type React from 'react';
import type { SetupStep } from '../../../types/setup';
import type { SectionProps } from './_shared';
import { DemographicsSection } from './DemographicsSection';
import { ContactsSection } from './ContactsSection';
import { ExtensionsSection } from './ExtensionsSection';

/**
 * Maps a `patient.*` step id → the inline-form section that owns it, and the
 * sub-field to highlight when the user navigated in from that specific step.
 *
 * Multiple step ids can map to the same section (e.g. address / telecom /
 * emergency_contact all live in ContactsSection). The backend still tracks
 * completion per-step; the section just renders the cohesive group.
 *
 * Adding a section = one entry here + a new component. The wizard page +
 * StepRenderer are untouched.
 */
export interface SectionEntry {
  Component: React.FC<SectionProps>;
  /** Sub-field to highlight (passed as `activeField`). */
  field?: string;
}

export const INLINE_SECTIONS: Record<string, SectionEntry> = {
  'patient.birth_date': { Component: DemographicsSection },
  'patient.address': { Component: ContactsSection, field: 'address' },
  'patient.telecom': { Component: ContactsSection, field: 'telecom' },
  'patient.emergency_contact': { Component: ContactsSection, field: 'emergency_contact' },
  'patient.race_or_ethnicity': { Component: ExtensionsSection, field: 'race_or_ethnicity' },
  'patient.preferred_language': { Component: ExtensionsSection, field: 'preferred_language' },
  'patient.insurance_provider': { Component: ExtensionsSection, field: 'insurance_provider' },
};

/**
 * Resolve the section for a step. Returns `null` when no section is
 * registered (the wizard then falls back to a placeholder).
 */
export function resolveSection(step: SetupStep): SectionEntry | null {
  return INLINE_SECTIONS[step.id] ?? null;
}
