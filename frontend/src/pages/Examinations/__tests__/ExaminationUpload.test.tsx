import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import React from 'react';

vi.mock('react-i18next', () => {
  const t = (key: string, fallback?: unknown) =>
    typeof fallback === 'string' ? fallback : String(key).split('.').pop();
  return {
    useTranslation: () => ({ t, i18n: { language: 'en' } }),
    initReactI18next: { type: '3rdParty', init: () => {} },
  };
});

vi.mock('../../../store/slices/patientSlice', () => ({
  usePatientStore: () => ({ currentPatient: { id: 'p1', name: 'Test Patient' } }),
}));

vi.mock('../../../store/slices/uiSlice', () => {
  const state = {
    showConfirmation: vi.fn(),
    setPageHeaderConfig: vi.fn(),
    pageHeaderConfig: null,
    currentExaminationId: null,
  };
  const useUIStore: any = (selector: any) => selector(state);
  useUIStore.getState = () => state;
  return { useUIStore };
});

vi.mock('../../../services/offlineService', () => ({
  offlineService: {
    saveDraft: vi.fn().mockResolvedValue(undefined),
    getDraft: vi.fn().mockResolvedValue(null),
    deleteDraft: vi.fn().mockResolvedValue(undefined),
  },
}));

vi.mock('../../../services/examinationService', () => ({
  createExamination: vi.fn().mockResolvedValue({ id: 'exam-1' }),
  getExaminationCategories: vi.fn().mockResolvedValue([]),
  createExaminationCategory: vi.fn(),
}));

vi.mock('../../../services/doctorService', () => ({
  listDoctors: vi.fn().mockResolvedValue([]),
  createDoctor: vi.fn(),
}));

vi.mock('../../../services/organizationService', () => ({
  listOrganizations: vi.fn().mockResolvedValue([]),
  createOrganization: vi.fn(),
}));

vi.mock('../../../services/documentService', () => ({
  uploadDocument: vi.fn(),
  getTempPreviewUrl: vi.fn(),
}));

vi.mock('../ExaminationGroupManager', () => ({
  ExaminationGroupManager: () => <div data-testid="group-manager" />,
}));

vi.mock('../../../components/ui/RichTextEditor', () => ({
  RichTextEditor: () => <div />,
}));
vi.mock('../../../components/ui/AIMagicFillModal', () => ({
  AIMagicFillModal: () => null,
}));
vi.mock('../../../components/ui/AIAssistButton', () => ({
  AIAssistButton: () => null,
}));

import ExaminationUpload from '../ExaminationUpload';

describe('ExaminationUpload (advanced page)', () => {
  it('opens in Smart mode and shows the smart/bulk workbench, not the single form', async () => {
    render(
      <MemoryRouter>
        <ExaminationUpload />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByTestId('group-manager')).toBeInTheDocument(),
    );
    expect(screen.queryByText('Examination Date')).not.toBeInTheDocument();
  });
});
