import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../../hooks/useBiomarkerPrecision', () => ({
  useBiomarkerPrecisionProfile: () => ({
    default: 0,
    below_30: 1,
    below_10: 1,
    below_3: 2,
    below_1: 3,
  }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, opts?: any) => opts?.defaultValue ?? key }),
}));

vi.mock('../../../../i18n', () => ({
  default: { t: (k: string) => k, changeLanguage: () => Promise.resolve() },
}));

vi.mock('../../../../hooks/useBiomarkers', () => {
  const mockBiomarkers = [
    {
      id: 'obs-1',
      displayName: 'Glucose',
      slug: 'glucose',
      definitionId: 'def-1',
      value: { raw: 5.23, normalized: 5.23 },
      unit: { rawSymbol: 'mmol/L', normalizedSymbol: 'mmol/L' },
      interpretation: 'Normal',
      referenceRange: { raw: '3.9 - 5.6', standard: { min: 3.9, max: 5.6 } },
      info: undefined,
      source: { date: '2026-06-20', name: 'Lab', type: 'examination' },
      isUnmapped: false,
    },
  ];
  return {
    useBiomarkers: () => ({
      biomarkers: mockBiomarkers,
      groupByCategory: () => ({ Lab: mockBiomarkers }),
      getGroupedData: () => [],
      getAbnormal: () => [],
      getTabs: () => [],
      totalCount: 1,
    }),
  };
});

import { ExaminationCard } from '../ExaminationCard';

describe('ExaminationCard precision', () => {
  it('formats biomarker value using the precision profile', () => {
    const props = {
      id: 'exam-card',
      isEditMode: false,
      data: {
        id: 'exam-1',
        category: 'Blood Test',
        examination_date: '2026-06-20',
        notes: 'Routine check',
      },
      documents: [],
    };

    // 5.23 → |5.23| is in [3,10) → below_10=1 → "5.2"
    // The value renders alongside unit; check it appears in the document.
    const { container } = render(
      <MemoryRouter>
        <ExaminationCard {...props} ref={null as any} />
      </MemoryRouter>
    );
    // Debug: check what rendered
    expect(container.textContent).toContain('5.2');
    expect(container.textContent).not.toContain('5.23');
  });
});
