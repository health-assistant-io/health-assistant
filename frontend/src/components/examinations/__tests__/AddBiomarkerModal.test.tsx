import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => {
  const t = (key: string, fallback?: unknown) =>
    typeof fallback === 'string' ? fallback : String(key).split('.').pop();
  return {
    useTranslation: () => ({ t, i18n: { language: 'en' } }),
    initReactI18next: { type: '3rdParty', init: () => {} },
  };
});

vi.mock('../../../services/observationService', () => ({
  createObservation: vi.fn().mockResolvedValue({ id: 'obs-1' }),
}));

vi.mock('../../../services/biomarkerService', () => ({
  __esModule: true,
  default: {
    getUnits: vi.fn().mockResolvedValue([]),
    getStates: vi.fn().mockResolvedValue([]),
    getAllBiomarkers: vi.fn().mockResolvedValue([]),
    getBiomarkerById: vi.fn().mockResolvedValue(null),
  },
}));

vi.mock('../../../services/catalogService', () => ({
  searchCatalogs: vi.fn().mockResolvedValue({ results: [] }),
  listCatalogItems: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  listCatalogTypes: vi.fn().mockResolvedValue({ types: [] }),
  createCatalogItem: vi.fn(),
  getCatalogItem: vi.fn().mockResolvedValue({}),
  getCatalogGraph: vi.fn(async () => ({ nodes: [], edges: [] })),
  getRelationType: vi.fn(),
  getRelationTypes: vi.fn(() => []),
  loadRelationTypes: vi.fn(async () => []),
}));

vi.mock('../../../services/conceptEdges', () => ({
  getLinkSchema: vi.fn().mockResolvedValue([]),
  createLinksFor: vi.fn().mockResolvedValue([]),
  selectionsToLinkInputs: vi.fn(() => []),
}));

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warn: vi.fn() },
}));

vi.mock('../../ui/RichTextEditor', () => ({ RichTextEditor: () => <div /> }));

import { AddBiomarkerModal } from '../AddBiomarkerModal';

describe('AddBiomarkerModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the shell header with one close and a disabled Save', async () => {
    const onClose = vi.fn();
    render(
      <AddBiomarkerModal
        isOpen
        onClose={onClose}
        patientId="p1"
        examinationId="exam-1"
        onSuccess={vi.fn()}
      />,
    );

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'close' })).toHaveLength(1);
    expect(screen.getByText('title')).toBeInTheDocument();
    expect(screen.getByText('manual_entry')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'add_result' })).toBeDisabled();
  });

  it('renders no inline create-definition action (definitions are created from the browse dialog)', async () => {
    render(
      <AddBiomarkerModal
        isOpen
        onClose={vi.fn()}
        patientId="p1"
        examinationId="exam-1"
        onSuccess={vi.fn()}
      />,
    );

    await screen.findByRole('dialog');
    // The old "create new definition" CTA is gone — the create path moved to
    // the browse dialog's New action (allowCreate on the picker).
    expect(
      screen.queryByRole('button', { name: /create_new_definition/i }),
    ).not.toBeInTheDocument();
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
  });
});
