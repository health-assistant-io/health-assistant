import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: any) => (typeof d === 'string' ? d : k) }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

vi.mock('../../../../services/examinationService', () => ({
  // Full exam incl. observations (the biomarker source) + documents.
  getExamination: vi.fn(async (id: string) => ({
    id,
    observations: [{ id: 'o1', code: { text: 'Glucose' } }],
  })),
  getExaminationDocuments: vi.fn(async () => [{ id: 'd1', filename: 'lab.pdf' }]),
}));

vi.mock('../../../../components/examinations/ExaminationCard', () => ({
  ExaminationCard: (props: any) => (
    <div
      data-testid={`card-${props.examination.id}`}
      data-selected={props.isSelected ? 'true' : 'false'}
      onClick={props.onClick}
    >
      {props.examination.id}
    </div>
  ),
}));
vi.mock('../../../../components/examinations/ExaminationPreview', () => ({
  ExaminationPreview: (props: any) => (
    <div data-testid="preview">{props.selectedExam?.id}</div>
  ),
}));

import { ExaminationView } from '../ExaminationView';
import { getExamination } from '../../../../services/examinationService';
import type { Examination } from '../../../../types/clinical';

const items: Examination[] = [
  { id: 'e1', patient_id: 'p', examination_date: '2026-01-01' } as any,
  { id: 'e2', patient_id: 'p', examination_date: '2026-01-02' } as any,
];

describe('ExaminationView', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders one card per item (no checkbox) and selects the first for preview', async () => {
    render(
      <ExaminationView
        items={items}
        pickedIds={[]}
        onTogglePick={() => {}}
        loading={false}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => {}}
      />,
    );
    expect(screen.getByTestId('card-e1')).toBeInTheDocument();
    expect(screen.getByTestId('card-e2')).toBeInTheDocument();
    expect(screen.getByTestId('card-e1').getAttribute('data-selected')).toBe('true');
    // The preview populates once the full exam fetch resolves.
    await waitFor(() => expect(screen.getByTestId('preview').textContent).toBe('e1'));
  });

  it('each card has an overlay Add button that picks that exam', () => {
    const onTogglePick = vi.fn();
    render(
      <ExaminationView
        items={items}
        pickedIds={[]}
        onTogglePick={onTogglePick}
        loading={false}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => {}}
      />,
    );
    const addBtns = screen.getAllByRole('button', { name: 'Add' });
    fireEvent.click(addBtns[1]); // e2's overlay Add
    expect(onTogglePick).toHaveBeenCalledWith(items[1]);
  });

  it('select→add: click a card to preview, then "Add to selection" adds the previewed exam', async () => {
    const onTogglePick = vi.fn();
    render(
      <ExaminationView
        items={items}
        pickedIds={[]}
        onTogglePick={onTogglePick}
        loading={false}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('preview').textContent).toBe('e1'));
    fireEvent.click(screen.getByTestId('card-e2'));
    await waitFor(() => expect(screen.getByTestId('preview').textContent).toBe('e2'));
    fireEvent.click(screen.getByRole('button', { name: 'Add to selection' }));
    expect(onTogglePick).toHaveBeenCalledWith(items[1]);
  });

  it('shows Added state for a picked card and the preview toolbar', () => {
    render(
      <ExaminationView
        items={items}
        pickedIds={['e1']}
        onTogglePick={() => {}}
        loading={false}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => {}}
      />,
    );
    // e1 overlay shows Added; the preview toolbar (e1 previewed) shows Added.
    expect(screen.getAllByRole('button', { name: 'Added' }).length).toBeGreaterThanOrEqual(1);
  });

  it('fetches the full exam (with observations) so biomarkers render in the preview', async () => {
    render(
      <ExaminationView
        items={items}
        pickedIds={[]}
        onTogglePick={() => {}}
        loading={false}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => {}}
      />,
    );
    await waitFor(() => expect(getExamination).toHaveBeenCalledWith('e1'));
  });
});
