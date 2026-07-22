/**
 * Tests for the GenerateAnatomyGraphHandler — the HITL handler for
 * `generate_anatomy_graph` tasks.
 *
 * Covers:
 *   1. The summary renderer (target chip + node/edge counts post-resolution).
 *   2. The generate-on-mount flow (loading → draft populated).
 *   3. Generation failure → retry path.
 *   4. Approve: calls anatomyService.importGraph then resolveHitlTask
 *      with confirmed + stats, then onResolved.
 *   5. 403 on import → surfaces the admin-only error pill.
 *   6. Reject: resolveHitlTask dismissed + onResolved.
 *   7. Registry lookup returns the handler.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, dft?: string) => dft ?? k,
    i18n: { language: 'en' },
  }),
}));

// --- Mock the two services the handler depends on ---
const assistMock = vi.fn();
const importMock = vi.fn();
const resolveMock = vi.fn();

vi.mock('../../../../../services/aiAssistanceService', () => ({
  getAIAssistance: (...args: any[]) => assistMock(...args),
  resolveHitlTask: (...args: any[]) => resolveMock(...args),
}));

vi.mock('../../../../../services/anatomyService', () => ({
  anatomyService: {
    importGraph: (...args: any[]) => importMock(...args),
  },
}));

// ReactFlow (inside ConceptGraphView) needs ResizeObserver, which jsdom doesn't
// provide. Mock it as a lightweight stub so the handler's graph tab renders
// without pulling in the real viz engine.
vi.mock('../../../../ui/ConceptGraphView', () => ({
  ConceptGraphView: (props: any) => (
    <div data-testid="concept-graph-view" data-node-count={props.nodes?.length ?? 0}>
      Graph mock ({props.nodes?.length ?? 0} nodes)
    </div>
  ),
}));

import {
  GenerateAnatomyGraphHandler,
  renderAnatomyGraphSummary,
} from '../GenerateAnatomyGraphHandler';
import { getHitlHandler } from '../../registry';
import type { TaskInfo } from '../../../../../types/ai';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const baseTask = (target?: string): TaskInfo => ({
  schema_version: 1,
  proposal_id: 'p-anat-1',
  task_type: 'generate_anatomy_graph',
  title: 'Generate Anatomy Graph: Heart',
  status: 'proposed',
  proposed_payload: target === undefined ? {} : { target_structure: target },
  context: { patient_id: 'patient-1' },
  created_at: '2026-07-22T00:00:00Z',
  resolved: null,
});

const generatedGraph = {
  nodes: [
    { slug: 'heart', name: 'Heart', class_concept_slug: 'organ' },
    { slug: 'left-ventricle', name: 'Left Ventricle', class_concept_slug: 'organ-part' },
  ],
  edges: [
    { source_slug: 'left-ventricle', target_slug: 'heart', relation_type: 'PART_OF' },
  ],
};

beforeEach(() => {
  assistMock.mockReset();
  importMock.mockReset();
  resolveMock.mockReset();
  resolveMock.mockResolvedValue({ success: true });
});

// ---------------------------------------------------------------------------
// 1. Summary renderer
// ---------------------------------------------------------------------------

describe('renderAnatomyGraphSummary', () => {
  it('shows the target structure chip', () => {
    const { container } = render(<>{renderAnatomyGraphSummary(baseTask('Heart'))}</>);
    expect(container.textContent).toMatch(/Heart/);
  });

  it('renders nothing when no target and no resolved graph', () => {
    const { container } = render(<>{renderAnatomyGraphSummary(baseTask())}</>);
    expect(container.textContent).toBe('');
  });

  it('shows node/edge counts from the resolved final_payload', () => {
    const task = baseTask('Heart');
    task.status = 'confirmed';
    task.resolved = {
      final_payload: { target_structure: 'Heart', nodes: generatedGraph.nodes, edges: generatedGraph.edges },
      result: {},
      at: '2026-07-22T00:00:00Z',
    };
    const { container } = render(<>{renderAnatomyGraphSummary(task)}</>);
    expect(container.textContent).toMatch(/2 nodes/);
    expect(container.textContent).toMatch(/1 edges/);
  });

  it('shows existing-graph counts from the pre-flight snapshot', () => {
    const task = baseTask('Heart');
    task.proposed_payload = {
      target_structure: 'Heart',
      existing: { root_slug: 'heart', node_count: 8, edge_count: 12 },
    };
    const { container } = render(<>{renderAnatomyGraphSummary(task)}</>);
    expect(container.textContent).toMatch(/8 exist/);
    expect(container.textContent).toMatch(/12 edges/);
  });
});

// ---------------------------------------------------------------------------
// 2 & 3. Generate-on-mount + failure/retry
// ---------------------------------------------------------------------------

describe('GenerateAnatomyGraphHandler — generation flow', () => {
  it('calls getAIAssistance on mount and populates the editor', async () => {
    assistMock.mockResolvedValue({ success: true, suggested_data: generatedGraph });
    render(
      <GenerateAnatomyGraphHandler
        task={baseTask('Heart')}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    await waitFor(() => expect(assistMock).toHaveBeenCalledTimes(1));
    expect(assistMock).toHaveBeenCalledWith(
      expect.objectContaining({ task_type: 'define_anatomy_graph', user_input: 'Heart' }),
    );
    // No existing snapshot → no context passed.
    expect(assistMock).toHaveBeenCalledWith(
      expect.objectContaining({ context: undefined }),
    );
    // The draft populated (toggle appears).
    await waitFor(() => expect(screen.getByText('Table')).toBeTruthy());
  });

  it('opens instantly from a pre-generated payload (no client-side LLM call)', async () => {
    // schema v3: the tool generated at proposal time → the handler reads the
    // prefilled draft synchronously, skipping getAIAssistance entirely.
    const task = baseTask('Heart');
    task.proposed_payload = {
      target_structure: 'Heart',
      generated: generatedGraph,
    };
    render(
      <GenerateAnatomyGraphHandler
        task={task}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    // The toggle appears immediately (no spinner, no LLM call).
    await waitFor(() => expect(screen.getByText('Table')).toBeTruthy());
    // getAIAssistance was NOT called — the draft came from the payload.
    expect(assistMock).not.toHaveBeenCalled();
  });

  it('shows the generating spinner while awaiting the LLM', async () => {
    let resolveAssist: (v: any) => void = () => {};
    assistMock.mockReturnValue(new Promise((r) => { resolveAssist = r; }));
    render(
      <GenerateAnatomyGraphHandler
        task={baseTask('Heart')}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByText('Generating anatomy graph…')).toBeTruthy());
    // Unblock + clean up.
    act(() => resolveAssist({ success: true, suggested_data: generatedGraph }));
  });

  it('surfaces a retry button when generation fails', async () => {
    assistMock.mockResolvedValue({ success: false, message: 'LLM down' });
    render(
      <GenerateAnatomyGraphHandler
        task={baseTask('Heart')}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByText('Retry generation')).toBeTruthy());
  });

  it('re-invokes getAIAssistance on retry', async () => {
    assistMock
      .mockResolvedValueOnce({ success: false, message: 'fail' })
      .mockResolvedValueOnce({ success: true, suggested_data: generatedGraph });
    render(
      <GenerateAnatomyGraphHandler
        task={baseTask('Heart')}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByText('Retry generation')).toBeTruthy());
    fireEvent.click(screen.getByText('Retry generation'));
    await waitFor(() => expect(assistMock).toHaveBeenCalledTimes(2));
    // Editor now populated — the graph view mock shows the node count.
    await waitFor(() => expect(screen.getByTestId('concept-graph-view')).toBeTruthy());
  });

  it('errors when no target_structure is provided', async () => {
    render(
      <GenerateAnatomyGraphHandler
        task={baseTask('')}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    // assistMock must not be called when there is no target.
    await waitFor(() => expect(assistMock).not.toHaveBeenCalled());
  });

  it('passes the existing-graph snapshot as context for dedup generation', async () => {
    assistMock.mockResolvedValue({ success: true, suggested_data: generatedGraph });
    const task = baseTask('Heart');
    task.proposed_payload = {
      target_structure: 'Heart',
      existing: {
        root_slug: 'heart',
        node_count: 3,
        node_slugs: ['heart', 'left-ventricle', 'right-ventricle'],
      },
    };
    render(
      <GenerateAnatomyGraphHandler
        task={task}
        sessionId="s1"
        onResolved={() => {}}
        onCancel={() => {}}
      />,
    );
    await waitFor(() => expect(assistMock).toHaveBeenCalledTimes(1));
    // The existing snapshot flows through as context.existing so the backend
    // prompt tells the LLM to fill gaps, not duplicate.
    expect(assistMock).toHaveBeenCalledWith(
      expect.objectContaining({
        context: { existing: expect.objectContaining({ root_slug: 'heart' }) },
      }),
    );
  });
});

// ---------------------------------------------------------------------------
// 4 & 5. Approve + 403
// ---------------------------------------------------------------------------

describe('GenerateAnatomyGraphHandler — confirm', () => {
  it('imports the graph, resolves confirmed, and calls onResolved', async () => {
    assistMock.mockResolvedValue({ success: true, suggested_data: generatedGraph });
    importMock.mockResolvedValue({ nodes_added: 2, edges_added: 1 });
    const onResolved = vi.fn();
    render(
      <GenerateAnatomyGraphHandler
        task={baseTask('Heart')}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={() => {}}
      />,
    );
    await waitFor(() => screen.getByText('Table'));
    fireEvent.click(screen.getByText('Confirm & Import Graph'));

    await waitFor(() => expect(importMock).toHaveBeenCalledTimes(1));
    expect(importMock).toHaveBeenCalledWith(
      expect.objectContaining({
        nodes: expect.arrayContaining([expect.objectContaining({ slug: 'heart' })]),
        edges: expect.arrayContaining([expect.objectContaining({ relation_type: 'PART_OF' })]),
      }),
    );
    await waitFor(() => expect(resolveMock).toHaveBeenCalledTimes(1));
    expect(resolveMock).toHaveBeenCalledWith(
      's1',
      'p-anat-1',
      expect.objectContaining({
        status: 'confirmed',
        result: { stats: { nodes_added: 2, edges_added: 1 } },
      }),
    );
    expect(onResolved).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'confirmed' }),
    );
  });

  it('surfaces the admin-only error on 403', async () => {
    assistMock.mockResolvedValue({ success: true, suggested_data: generatedGraph });
    importMock.mockRejectedValue({ response: { status: 403, data: { detail: 'forbidden' } } });
    const onResolved = vi.fn();
    render(
      <GenerateAnatomyGraphHandler
        task={baseTask('Heart')}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={() => {}}
      />,
    );
    await waitFor(() => screen.getByText('Table'));
    fireEvent.click(screen.getByText('Confirm & Import Graph'));
    await waitFor(() =>
      expect(screen.getByText(/Only system admins can import anatomy graphs/i)).toBeTruthy(),
    );
    // No resolve, no onResolved — the card stays editable.
    expect(resolveMock).not.toHaveBeenCalled();
    expect(onResolved).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 6. Reject
// ---------------------------------------------------------------------------

describe('GenerateAnatomyGraphHandler — reject', () => {
  it('records dismissed and calls onResolved', async () => {
    assistMock.mockResolvedValue({ success: true, suggested_data: generatedGraph });
    const onResolved = vi.fn();
    render(
      <GenerateAnatomyGraphHandler
        task={baseTask('Heart')}
        sessionId="s1"
        onResolved={onResolved}
        onCancel={() => {}}
      />,
    );
    await waitFor(() => screen.getByText('Table'));
    fireEvent.click(screen.getByText('Reject'));
    await waitFor(() => expect(resolveMock).toHaveBeenCalledTimes(1));
    expect(resolveMock).toHaveBeenCalledWith(
      's1',
      'p-anat-1',
      expect.objectContaining({ status: 'dismissed' }),
    );
    expect(onResolved).toHaveBeenCalledWith(expect.objectContaining({ status: 'dismissed' }));
  });
});

// ---------------------------------------------------------------------------
// 7. Registry wiring
// ---------------------------------------------------------------------------

describe('registry wiring', () => {
  it('getHitlHandler returns the anatomy-graph handler', () => {
    const h = getHitlHandler('generate_anatomy_graph');
    expect(h).toBeTruthy();
    expect(h?.taskType).toBe('generate_anatomy_graph');
    expect(h?.FormComponent).toBe(GenerateAnatomyGraphHandler);
  });
});
