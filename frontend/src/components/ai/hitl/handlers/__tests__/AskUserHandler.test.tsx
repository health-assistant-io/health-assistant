/**
 * Tests for the AskUserHandler — the inline HITL handler for `ask_user` tasks.
 *
 * Covers:
 *   1. The pure validation helpers (`buildDefaults`, `validate` via the
 *      exposed render paths).
 *   2. The full render path for each of the five question kinds.
 *   3. Submit/Skip behavior against a mocked `resolveHitlTask`.
 *   4. The `renderAskUserSummary` chip renderer.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, dftOrOpts?: any) => {
      let s: string;
      let opts: Record<string, unknown>;
      if (typeof dftOrOpts === 'string') {
        s = dftOrOpts;
        opts = {};
      } else {
        s = dftOrOpts?.defaultValue ?? k;
        opts = dftOrOpts ?? {};
      }
      for (const [key, val] of Object.entries(opts)) {
        if (key === 'defaultValue') continue;
        s = s.replace(new RegExp(`{{\\s*${key}\\s*}}`, 'g'), String(val));
      }
      return s;
    },
    i18n: { language: 'en' },
  }),
}));

const resolveMock = vi.fn();
vi.mock('../../../../../services/aiAssistanceService', () => ({
  resolveHitlTask: (...args: any[]) => resolveMock(...args),
}));

// Capture the props the handler passes into the catalog/instance pickers so
// we can assert wiring without depending on the pickers' internal rendering
// (which has its own test coverage in catalog/__tests__ and instances/__tests__).
const catalogPickerProps: any[] = [];
const instancePickerProps: any[] = [];

vi.mock('../../../../catalog/CatalogItemPicker', () => ({
  CatalogItemPicker: (props: any) => {
    catalogPickerProps.push(props);
    return (
      <div data-testid="catalog-picker">
        <span data-testid="catalog-mode">{props.mode}</span>
        <span data-testid="catalog-types">{(props.allowedTypes || []).join(',')}</span>
        <span data-testid="catalog-kind">{props.conceptKind ?? ''}</span>
        <span data-testid="catalog-value">
          {(props.value || []).map((v: any) => `${v.type}:${v.id}`).join('|')}
        </span>
      </div>
    );
  },
}));

vi.mock('../../../../instances/InstancePicker', () => ({
  InstancePicker: (props: any) => {
    instancePickerProps.push(props);
    return (
      <div data-testid="instance-picker">
        <span data-testid="instance-mode">{props.mode}</span>
        <span data-testid="instance-types">{(props.allowedTypes || []).join(',')}</span>
        <span data-testid="instance-patient">{props.patientId ?? ''}</span>
        <span data-testid="instance-value">
          {(props.value || []).map((v: any) => `${v.type}:${v.id}`).join('|')}
        </span>
      </div>
    );
  },
}));

import {
  AskUserHandler,
  renderAskUserSummary,
} from '../AskUserHandler';
import type { TaskInfo, AskUserQuestion } from '../../../../../types/ai';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const baseTask = (questions: AskUserQuestion[]): TaskInfo => ({
  schema_version: 2,
  proposal_id: 'p-test-1',
  task_type: 'ask_user',
  title: 'Quick questions',
  status: 'proposed',
  proposed_payload: { summary: 'Need a few details.', questions },
  context: { patient_id: 'patient-1' },
  created_at: '2026-07-21T00:00:00Z',
  resolved: null,
});

beforeEach(() => {
  resolveMock.mockReset();
  resolveMock.mockResolvedValue({ success: true });
  catalogPickerProps.length = 0;
  instancePickerProps.length = 0;
});

// ---------------------------------------------------------------------------
// 1. Summary renderer
// ---------------------------------------------------------------------------

describe('renderAskUserSummary', () => {
  it('shows the question count', () => {
    const { container } = render(
      <>{renderAskUserSummary(baseTask([{ id: 'q1', kind: 'freetext', prompt: 'X' }]))}</>,
    );
    expect(container.textContent).toMatch(/1 question/);
  });

  it('pluralises for multiple questions', () => {
    const { container } = render(
      <>{renderAskUserSummary(
        baseTask([
          { id: 'q1', kind: 'freetext', prompt: 'X' },
          { id: 'q2', kind: 'freetext', prompt: 'Y' },
        ]),
      )}</>,
    );
    expect(container.textContent).toMatch(/2 questions/);
  });

  it('renders nothing when there are no questions', () => {
    const { container } = render(<>{renderAskUserSummary(baseTask([]))}</>);
    expect(container.textContent).toBe('');
  });
});

// ---------------------------------------------------------------------------
// 2. Question-kind rendering
// ---------------------------------------------------------------------------

describe('AskUserHandler — question kinds', () => {
  it('renders a freetext question with placeholder', () => {
    render(
      <AskUserHandler
        task={baseTask([
          {
            id: 'q1',
            kind: 'freetext',
            prompt: 'What dosage?',
            placeholder: '500 mg',
            required: true,
          },
        ])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText('What dosage?')).toBeTruthy();
    expect(screen.getByPlaceholderText('500 mg')).toBeTruthy();
    // required asterisk
    expect(screen.getByText('*')).toBeTruthy();
  });

  it('renders all single_choice options', () => {
    render(
      <AskUserHandler
        task={baseTask([
          {
            id: 'q1',
            kind: 'single_choice',
            prompt: 'Route?',
            options: [
              { value: 'oral', label: 'Oral' },
              { value: 'iv', label: 'Intravenous' },
            ],
          },
        ])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText('Oral')).toBeTruthy();
    expect(screen.getByText('Intravenous')).toBeTruthy();
    expect(screen.getAllByRole('radio').length).toBe(2);
  });

  it('renders all multi_choice options as checkboxes', () => {
    render(
      <AskUserHandler
        task={baseTask([
          {
            id: 'q1',
            kind: 'multi_choice',
            prompt: 'Pick many',
            options: [
              { value: 'a', label: 'A' },
              { value: 'b', label: 'B' },
              { value: 'c', label: 'C' },
            ],
          },
        ])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getAllByRole('checkbox').length).toBe(3);
  });

  it('renders the catalog_ref via CatalogItemPicker with the right props', () => {
    render(
      <AskUserHandler
        task={baseTask([
          {
            id: 'q1',
            kind: 'catalog_ref',
            prompt: 'Which biomarker?',
            catalog_type: 'biomarker',
            multi: false,
          },
        ])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByTestId('catalog-picker')).toBeTruthy();
    expect(screen.getByTestId('catalog-mode').textContent).toBe('single');
    expect(screen.getByTestId('catalog-types').textContent).toBe('biomarker');
    // No concept-kind filter for a plain biomarker catalog.
    expect(screen.getByTestId('catalog-kind').textContent).toBe('');
  });

  it('maps clinical_event_type → concept + event_category conceptKind', () => {
    render(
      <AskUserHandler
        task={baseTask([
          {
            id: 'q1',
            kind: 'catalog_ref',
            prompt: 'Which event type?',
            catalog_type: 'clinical_event_type',
          },
        ])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByTestId('catalog-types').textContent).toBe('concept');
    expect(screen.getByTestId('catalog-kind').textContent).toBe('event_category');
  });

  it('renders the instance_ref via InstancePicker with the right props', () => {
    render(
      <AskUserHandler
        task={baseTask([
          {
            id: 'q1',
            kind: 'instance_ref',
            prompt: 'Which event?',
            entity_type: 'clinical_event',
            multi: true,
          },
        ])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByTestId('instance-picker')).toBeTruthy();
    expect(screen.getByTestId('instance-mode').textContent).toBe('multi');
    // clinical_event → event (frontend InstanceType).
    expect(screen.getByTestId('instance-types').textContent).toBe('event');
    // patient_id from the task context is forwarded.
    expect(screen.getByTestId('instance-patient').textContent).toBe('patient-1');
  });

  it('renders an "unsupported" notice for unknown kinds (defensive)', () => {
    // Bypass TS to exercise the runtime fallback branch.
    const unknown = { id: 'q1', kind: 'rating', prompt: 'Stars?' } as unknown as AskUserQuestion;
    render(
      <AskUserHandler
        task={baseTask([unknown])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/Unsupported question type: rating/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 3. Submit / Skip behaviour
// ---------------------------------------------------------------------------

describe('AskUserHandler — submit & skip', () => {
  it('disables Submit while a required freetext is empty', () => {
    render(
      <AskUserHandler
        task={baseTask([
          { id: 'q1', kind: 'freetext', prompt: 'Required', required: true },
        ])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    const submit = screen.getByText('Submit answers').closest('button');
    expect(submit?.disabled).toBe(true);
  });

  it('enables Submit once the required field has a value', () => {
    render(
      <AskUserHandler
        task={baseTask([
          { id: 'q1', kind: 'freetext', prompt: 'Required', required: true },
        ])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    const input = screen.getByPlaceholderText('') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '500 mg' } });
    const submit = screen.getByText('Submit answers').closest('button');
    expect(submit?.disabled).toBe(false);
  });

  it('on Submit: calls resolveHitlTask with confirmed + answers, then onResolved', async () => {
    const onResolved = vi.fn();
    render(
      <AskUserHandler
        task={baseTask([
          { id: 'q_dose', kind: 'freetext', prompt: 'Dose?', required: true },
        ])}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={() => {}}
      />,
    );
    const input = screen.getByPlaceholderText('') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '500 mg' } });
    fireEvent.click(screen.getByText('Submit answers'));

    await waitFor(() => expect(resolveMock).toHaveBeenCalledTimes(1));
    expect(resolveMock).toHaveBeenCalledWith(
      's1',
      'p-test-1',
      expect.objectContaining({
        status: 'confirmed',
        final_payload: { answers: { q_dose: '500 mg' } },
      }),
    );
    expect(onResolved).toHaveBeenCalledWith(
      expect.objectContaining({
        status: 'confirmed',
        resolved: expect.objectContaining({
          final_payload: { answers: { q_dose: '500 mg' } },
        }),
      }),
    );
  });

  it('on Skip: calls resolveHitlTask with dismissed, then onResolved', async () => {
    const onResolved = vi.fn();
    render(
      <AskUserHandler
        task={baseTask([
          { id: 'q1', kind: 'freetext', prompt: 'Optional' },
        ])}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('Skip questions'));
    await waitFor(() => expect(resolveMock).toHaveBeenCalledTimes(1));
    expect(resolveMock).toHaveBeenCalledWith(
      's1',
      'p-test-1',
      expect.objectContaining({ status: 'dismissed' }),
    );
    expect(onResolved).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'dismissed' }),
    );
  });

  it('multi_choice min_select blocks submit until satisfied', () => {
    render(
      <AskUserHandler
        task={baseTask([
          {
            id: 'q1',
            kind: 'multi_choice',
            prompt: 'Pick ≥2',
            options: [
              { value: 'a', label: 'A' },
              { value: 'b', label: 'B' },
            ],
            min_select: 2,
            required: true,
          },
        ])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    // No selection → submit disabled.
    let submit = screen.getByText('Submit answers').closest('button');
    expect(submit?.disabled).toBe(true);
    // Select one → still disabled.
    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);
    submit = screen.getByText('Submit answers').closest('button');
    expect(submit?.disabled).toBe(true);
    // Select second → enabled.
    fireEvent.click(checkboxes[1]);
    submit = screen.getByText('Submit answers').closest('button');
    expect(submit?.disabled).toBe(false);
  });

  it('renders empty-state notice when there are no questions', () => {
    render(
      <AskUserHandler
        task={baseTask([])}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/did not include any questions/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 4. Rich-fields candidate cache (LLM gets full metadata per pick)
// ---------------------------------------------------------------------------

describe('AskUserHandler — rich candidate metadata', () => {
  it('CatalogItemPicker onChange forwards the rich candidate from the snapshot', async () => {
    // The candidate snapshot from the backend carries code/coding_system/
    // category/is_telemetry/unit/description. When the user picks an id
    // present in the snapshot, those fields MUST flow through to the answer.
    const richCandidate = {
      id: 'u1',
      name: 'HbA1c',
      slug: 'hba1c',
      type: 'biomarker',
      code: '4548-4',
      coding_system: 'loinc',
      category: 'Hematology',
      is_telemetry: false,
      unit: '%',
      description: 'Glycated hemoglobin',
    };
    const onResolved = vi.fn();
    render(
      <AskUserHandler
        task={baseTask([
          {
            id: 'q1',
            kind: 'catalog_ref',
            prompt: 'Which biomarker?',
            catalog_type: 'biomarker',
            candidates: [richCandidate],
          },
        ])}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={() => {}}
      />,
    );

    // The last CatalogItemPicker props capture contains its onChange; invoke
    // it as the picker would when the user selects an item. Wrap in act() so
    // the state update flushes before the submit click below.
    const lastCall = catalogPickerProps[catalogPickerProps.length - 1];
    expect(lastCall).toBeTruthy();
    act(() => {
      lastCall.onChange([{ type: 'biomarker', id: 'u1', label: 'HbA1c' }]);
    });

    // Submit (the question is not required, so submit is enabled).
    await waitFor(() => {
      fireEvent.click(screen.getByText('Submit answers'));
    });
    await waitFor(() => expect(resolveMock).toHaveBeenCalled());

    // The final_payload.answers must carry the rich fields, not just id+name.
    expect(resolveMock).toHaveBeenCalledWith(
      's1',
      'p-test-1',
      expect.objectContaining({
        status: 'confirmed',
        final_payload: {
          answers: {
            q1: richCandidate,
          },
        },
      }),
    );
  });

  it('falls back to identification-only when the picked id is NOT in the snapshot', async () => {
    // The user types and picks an item that wasn't pre-resolved server-side.
    // The handler emits identification-only; the LLM can re-fetch if needed.
    const onResolved = vi.fn();
    render(
      <AskUserHandler
        task={baseTask([
          {
            id: 'q1',
            kind: 'catalog_ref',
            prompt: 'Which biomarker?',
            catalog_type: 'biomarker',
            candidates: [
              { id: 'u1', name: 'HbA1c', type: 'biomarker' }, // only this one is cached
            ],
          },
        ])}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={() => {}}
      />,
    );

    const lastCall = catalogPickerProps[catalogPickerProps.length - 1];
    // User picks a DIFFERENT id (live search).
    act(() => {
      lastCall.onChange([{ type: 'biomarker', id: 'u-other', label: 'Glucose' }]);
    });

    await waitFor(() => {
      fireEvent.click(screen.getByText('Submit answers'));
    });
    await waitFor(() => expect(resolveMock).toHaveBeenCalled());

    expect(resolveMock).toHaveBeenCalledWith(
      's1',
      'p-test-1',
      expect.objectContaining({
        status: 'confirmed',
        final_payload: {
          answers: {
            q1: { id: 'u-other', name: 'Glucose', type: 'biomarker' },
          },
        },
      }),
    );
  });

  it('instance_ref onChange preserves date+status from the snapshot', async () => {
    const richInstance = {
      id: 'e1',
      name: 'Knee Surgery Recovery',
      type: 'clinical_event',
      status: 'RESOLVED',
      date: '2024-03-15',
    };
    const onResolved = vi.fn();
    render(
      <AskUserHandler
        task={baseTask([
          {
            id: 'q1',
            kind: 'instance_ref',
            prompt: 'Pick an event',
            entity_type: 'clinical_event',
            candidates: [richInstance],
          },
        ])}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={() => {}}
      />,
    );

    const lastCall = instancePickerProps[instancePickerProps.length - 1];
    // The picker emits the frontend InstanceType ('event'); the cache lookup
    // is keyed by id alone so type-string mismatches don't matter.
    act(() => {
      lastCall.onChange([
        { type: 'event', id: 'e1', label: 'Knee Surgery Recovery' },
      ]);
    });

    await waitFor(() => {
      fireEvent.click(screen.getByText('Submit answers'));
    });
    await waitFor(() => expect(resolveMock).toHaveBeenCalled());

    expect(resolveMock).toHaveBeenCalledWith(
      's1',
      'p-test-1',
      expect.objectContaining({
        status: 'confirmed',
        final_payload: {
          answers: {
            q1: richInstance,
          },
        },
      }),
    );
  });
});
