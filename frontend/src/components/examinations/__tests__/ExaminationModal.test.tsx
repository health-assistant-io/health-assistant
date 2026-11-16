import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('react-i18next', () => {
  const t = (key: string, fallback?: unknown) =>
    typeof fallback === 'string' ? fallback : String(key).split('.').pop();
  return {
    useTranslation: () => ({ t, i18n: { language: 'en' } }),
    initReactI18next: { type: '3rdParty', init: () => {} },
  };
});

const createExamination = vi.hoisted(() =>
  vi.fn().mockResolvedValue({ id: 'exam-1' }),
);
const uploadDocument = vi.hoisted(() => vi.fn().mockResolvedValue({}));

vi.mock('../../../services/examinationService', () => ({
  createExamination,
  getExaminationCategories: vi.fn().mockResolvedValue([]),
}));

vi.mock('../../../services/doctorService', () => ({
  listDoctors: vi.fn().mockResolvedValue([]),
  createDoctor: vi.fn().mockResolvedValue({ id: 'doc-1', name: 'Smith' }),
}));

vi.mock('../../../services/organizationService', () => ({
  listOrganizations: vi.fn().mockResolvedValue([]),
  createOrganization: vi.fn().mockResolvedValue({ id: 'org-1', name: 'Clinic' }),
}));

vi.mock('../../../services/documentService', () => ({
  uploadDocument,
  getTempPreviewUrl: vi.fn().mockResolvedValue({ url: 'blob:x' }),
}));

vi.mock('../../ui/RichTextEditor', () => ({ RichTextEditor: () => <div /> }));
vi.mock('../../ui/AIAssistButton', () => ({ AIAssistButton: () => null }));

import { ExaminationModal } from '../ExaminationModal';

const renderModal = (props: Partial<React.ComponentProps<typeof ExaminationModal>> = {}) => {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  const onAdvanced = vi.fn();
  render(
    <ExaminationModal
      isOpen
      onClose={onClose}
      patientId="p1"
      onSuccess={onSuccess}
      onAdvanced={onAdvanced}
      {...props}
    />,
  );
  return { onClose, onSuccess, onAdvanced };
};

describe('ExaminationModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createExamination.mockResolvedValue({ id: 'exam-1' });
    uploadDocument.mockResolvedValue({});
  });

  it('renders the shell header with one close and the full form', async () => {
    renderModal();

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'close' })).toHaveLength(1);
    expect(screen.getByText('new_examination')).toBeInTheDocument();
    expect(screen.getByText('Upload documents and record the visit')).toBeInTheDocument();
    expect(screen.getByText('Examination Date')).toBeInTheDocument();
    expect(screen.getByText('Documents')).toBeInTheDocument();
    expect(screen.getByText('Clinical Notes')).toBeInTheDocument();
    expect(screen.getByText('Patient Notes')).toBeInTheDocument();
    expect(screen.getByText('Smart & bulk upload')).toBeInTheDocument();
  });

  it('saves a visit without documents from the default date', async () => {
    const user = userEvent.setup();
    const { onSuccess, onClose } = renderModal();

    await screen.findByRole('dialog');
    const save = screen.getByRole('button', { name: 'Save Examination' });
    expect(save).toBeEnabled();

    await user.click(save);

    await waitFor(() => expect(createExamination).toHaveBeenCalledTimes(1));
    const payload = createExamination.mock.calls[0][0];
    expect(payload.patient_id).toBe('p1');
    expect(payload.examination_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(payload.auto_extract_metadata).toBe(false);
    expect(payload.doctor_ids).toEqual([]);
    expect(uploadDocument).not.toHaveBeenCalled();
    expect(onSuccess).toHaveBeenCalledWith('exam-1');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('uploads every attached document after creating the examination', async () => {
    const user = userEvent.setup();
    const { onSuccess } = renderModal();

    await screen.findByRole('dialog');
    const input = document.getElementById(
      'examination-file-upload',
    ) as HTMLInputElement;
    const file = new File(['report'], 'report.pdf', { type: 'application/pdf' });
    fireEvent.change(input, { target: { files: [file] } });

    await user.click(screen.getByRole('button', { name: 'Save Examination' }));

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(1));
    expect(uploadDocument.mock.calls[0][0]).toBe(file);
    expect(uploadDocument.mock.calls[0][1]).toBe('p1');
    expect(uploadDocument.mock.calls[0][2]).toBe('exam-1');
    expect(uploadDocument.mock.calls[0][3]).toBe(true);
    expect(onSuccess).toHaveBeenCalledWith('exam-1');
  });

  it('closes without writing on Cancel', async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal();

    await screen.findByRole('dialog');
    await user.click(screen.getByRole('button', { name: 'cancel' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(createExamination).not.toHaveBeenCalled();
  });

  it('hands off to the smart/bulk upload page', async () => {
    const user = userEvent.setup();
    const { onAdvanced } = renderModal();

    await screen.findByRole('dialog');
    await user.click(screen.getByRole('button', { name: /smart & bulk upload/i }));

    expect(onAdvanced).toHaveBeenCalledTimes(1);
    expect(createExamination).not.toHaveBeenCalled();
  });
});
