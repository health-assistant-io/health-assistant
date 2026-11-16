import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

vi.mock('react-i18next', () => {
  const t = (key: string, opts?: unknown) => {
    if (typeof opts === 'string') return opts;
    if (opts && typeof opts === 'object' && 'defaultValue' in (opts as object)) {
      return String((opts as { defaultValue: string }).defaultValue).replace(
        /\{\{(\w+)\}\}/g,
        (_m, name: string) => String((opts as Record<string, unknown>)[name] ?? `{{${name}}}`)
      );
    }
    return key;
  };
  return { useTranslation: () => ({ t, i18n: { language: 'en' } }) };
});

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const EVENTS = [
  {
    id: 'e1',
    tenant_id: 't1',
    user_id: '11111111-1111-1111-1111-111111111111',
    action: 'tenant.security_policy',
    resource_type: 'tenant',
    resource_id: 't1',
    outcome: 'ok',
    old_value: { mfa_required_roles: ['ADMIN'] }, // # gate-allow: *, audit diff
    new_value: { mfa_required_roles: ['ADMIN', 'MANAGER'] },
    created_at: '2026-11-09T10:00:00Z',
    chain_key: 'tenant:t1',
    seq: 7,
    prev_hash: 'aaaaaaaaaaaaaaaaaaaa',
    entry_hash: 'bbbbbbbbbbbbbbbbbbbb',
  },
  {
    id: 'e2',
    tenant_id: null,
    user_id: null,
    action: 'auth.login',
    resource_type: 'session',
    resource_id: null,
    outcome: 'denied',
    old_value: null, // # gate-allow: *, audit diff
    new_value: { detail: '[redacted]' },
    created_at: '2026-11-09T09:00:00Z',
    chain_key: 'system',
    seq: 3,
    prev_hash: 'cccccccccccccccccccc',
    entry_hash: 'dddddddddddddddddddd',
  },
];

vi.mock('../../../services/adminAuditService', () => ({
  listAuditEvents: vi.fn(),
  exportAuditEvents: vi.fn(),
  getAuditRetention: vi.fn(),
  verifyAuditChain: vi.fn(),
}));

vi.mock('../../../services/tenantService', () => ({
  listTenants: vi.fn(),
}));

import { toast } from 'react-toastify';
import {
  exportAuditEvents,
  getAuditRetention,
  listAuditEvents,
  verifyAuditChain,
} from '../../../services/adminAuditService';
import { listTenants } from '../../../services/tenantService';
import { useAccessStore } from '../../../store/slices/accessSlice';
import AuditViewer from '../AuditViewer';

function seedAccess(roles: string[]) {
  useAccessStore.setState({
    permissions: new Set(['audit:read', 'settings:read']),
    roles,
    tenant: { id: 't1', name: 'Clinic' },
    patientGrants: [],
    pendingGrants: [],
    isHydrated: true,
    hasError: false,
  });
}

function renderViewer() {
  return render(
    <MemoryRouter>
      <AuditViewer />
    </MemoryRouter>
  );
}

describe('AuditViewer (admin audit surface, ADR 0054 phase 3 UI)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listAuditEvents).mockResolvedValue({ items: EVENTS, total: 52 } as never);
    vi.mocked(listTenants).mockResolvedValue({
      items: [{ id: 't1', name: 'Clinic', slug: 'clinic', is_active: true, settings: {} }],
      total: 1,
    } as never);
    vi.mocked(getAuditRetention).mockResolvedValue({
      policies: [
        {
          scope: 'table',
          scope_value: 'audit_events',
          action: 'anonymize',
          days: 3650,
          timestamp_column: 'created_at',
          enabled: true,
        },
      ],
      legal_holds: null,
      notes: 'Audit rows are never deleted.',
    } as never);
    seedAccess(['ADMIN']);
  });

  it('renders rows with chain metadata and expands the diff viewer', async () => {
    const user = userEvent.setup();
    renderViewer();

    expect((await screen.findAllByTestId('audit-row')).length).toBeGreaterThan(0);
    expect(screen.getAllByTestId('audit-row')).toHaveLength(2);
    expect(screen.getAllByTestId('audit-entry_hash')[0].querySelector('code')).toHaveTextContent(/^bbbbbbbbbb…$/);
    expect(screen.getByTestId('audit-total')).toHaveTextContent('admin.audit.total');

    await user.click(screen.getAllByTestId('audit-row-toggle')[0]);
    expect(screen.getByTestId('audit-diff-new_value')).toHaveTextContent('mfa_required_roles');
    expect(screen.getByTestId('audit-diff-old_value')).toHaveTextContent('mfa_required_roles'); // # gate-allow: *, audit diff
    expect(screen.getByTestId('audit-chain-key')).toHaveTextContent('tenant:t1');

    // The redaction marker renders as redacted, never the raw marker value.
    await user.click(screen.getAllByTestId('audit-row-toggle')[1]);
    expect(screen.getByText('admin.audit.redacted')).toBeInTheDocument();
  });

  it('passes filters to the list call', async () => {
    const user = userEvent.setup();
    renderViewer();

    await screen.findAllByTestId('audit-row');
    await user.type(screen.getByLabelText('admin.audit.filter_action'), 'auth.');
    await user.selectOptions(screen.getByTestId('audit-outcome-filter'), 'denied');

    await waitFor(() => {
      const calls = vi.mocked(listAuditEvents).mock.calls;
      const last = calls[calls.length - 1]![0]!;
      expect(last.action).toBe('auth.');
      expect(last.outcome).toBe('denied');
      expect(last.all).toBeUndefined();
      expect(last.tenant_id).toBeNull();
    });
  });

  it('paginates with limit and offset', async () => {
    const user = userEvent.setup();
    renderViewer();

    await screen.findAllByTestId('audit-row');
    expect(screen.getByTestId('audit-prev')).toBeDisabled();
    await user.click(screen.getByTestId('audit-next'));

    await waitFor(() => {
      const calls = vi.mocked(listAuditEvents).mock.calls;
      const last = calls[calls.length - 1]![0]!;
      expect(last.offset).toBe(50);
      expect(last.limit).toBe(50);
    });
  });

  it('exports CSV and JSON with the active filters', async () => {
    const user = userEvent.setup();
    renderViewer();

    await screen.findAllByTestId('audit-row');
    await user.click(screen.getByTestId('audit-export-csv'));
    await user.click(screen.getByTestId('audit-export-json'));

    await waitFor(() => {
      expect(exportAuditEvents).toHaveBeenCalledWith(expect.objectContaining({ limit: 50 }), 'csv');
      expect(exportAuditEvents).toHaveBeenCalledWith(expect.objectContaining({ limit: 50 }), 'json');
    });
  });

  it('hides the tenant picker, all toggle, verify and retention for ADMIN', async () => {
    renderViewer();

    await screen.findAllByTestId('audit-row');
    expect(screen.queryByTestId('audit-tenant-picker')).not.toBeInTheDocument();
    expect(screen.queryByTestId('audit-all-toggle')).not.toBeInTheDocument();
    expect(screen.queryByTestId('audit-verify')).not.toBeInTheDocument();
    expect(screen.queryByTestId('audit-retention-card')).not.toBeInTheDocument();
  });

  it('picks a tenant, toggles system-wide and verifies chains for SYSTEM_ADMIN', async () => {
    const user = userEvent.setup();
    vi.mocked(verifyAuditChain).mockResolvedValue({
      chain: { chain_key: 'tenant:t1', ok: true, entries: 7, head_seq: 7, first_bad_seq: null, reason: 'ok' },
    } as never);
    seedAccess(['SYSTEM_ADMIN']);
    renderViewer();

    await screen.findByTestId('audit-tenant-picker');
    expect(screen.getByTestId('audit-retention-card')).toBeInTheDocument();

    await user.selectOptions(screen.getByTestId('audit-tenant-picker'), 't1');
    await waitFor(() => {
      const calls = vi.mocked(listAuditEvents).mock.calls;
      expect(calls[calls.length - 1]![0]!.tenant_id).toBe('t1');
    });

    await user.click(screen.getByTestId('audit-verify'));
    expect(await screen.findByTestId('audit-verify-banner')).toHaveTextContent('tenant:t1');
    expect(verifyAuditChain).toHaveBeenCalledWith({ tenantId: 't1', all: false });

    await user.click(screen.getByTestId('audit-all-toggle'));
    await waitFor(() => {
      const calls = vi.mocked(listAuditEvents).mock.calls;
      const last = calls[calls.length - 1]![0]!;
      expect(last.all).toBe(true);
      expect(last.tenant_id).toBeNull();
    });
  });

  it('shows a broken-chain banner when verification fails', async () => {
    const user = userEvent.setup();
    vi.mocked(verifyAuditChain).mockResolvedValue({
      chains: [
        { chain_key: 'tenant:t1', ok: false, entries: 7, head_seq: 7, first_bad_seq: 4, reason: 'gap' },
      ],
    } as never);
    seedAccess(['SYSTEM_ADMIN']);
    renderViewer();

    await screen.findByTestId('audit-tenant-picker');
    await user.click(screen.getByTestId('audit-verify'));

    const banner = await screen.findByTestId('audit-verify-banner');
    expect(banner).toHaveTextContent('admin.audit.verify_failed');
    expect(banner).toHaveTextContent('gap');
    expect(banner).toHaveTextContent('admin.audit.first_bad_seq');
  });

  it('shows the error state when the stream fails to load', async () => {
    vi.mocked(listAuditEvents).mockRejectedValue({
      response: { status: 403, data: { detail: 'The system-wide audit view requires SYSTEM_ADMIN.' } },
    });
    renderViewer();

    expect(await screen.findByTestId('audit-error')).toHaveTextContent(
      'The system-wide audit view requires SYSTEM_ADMIN.'
    );
    expect(screen.getByTestId('audit-empty')).toBeInTheDocument();
  });

  it('shows the empty state when no entries match', async () => {
    vi.mocked(listAuditEvents).mockResolvedValue({ items: [], total: 0 } as never);
    renderViewer();

    expect(await screen.findByTestId('audit-empty')).toHaveTextContent('admin.audit.empty');
  });

  it('shows the retention card with the null legal-holds note', async () => {
    seedAccess(['SYSTEM_ADMIN']);
    renderViewer();

    const card = await screen.findByTestId('audit-retention-card');
    expect(card).toHaveTextContent('audit_events');
    expect(screen.getByTestId('audit-legal-holds')).toHaveTextContent('admin.audit.legal_holds_null');
  });

  it('toasts an export failure', async () => {
    const user = userEvent.setup();
    vi.mocked(exportAuditEvents).mockRejectedValue({
      response: { status: 403, data: { detail: 'denied' } },
    });
    renderViewer();

    await screen.findAllByTestId('audit-row');
    await user.click(screen.getByTestId('audit-export-json'));
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('denied');
    });
  });

  it('warns when the export was truncated at the row cap', async () => {
    const user = userEvent.setup();
    vi.mocked(exportAuditEvents).mockResolvedValue({ truncated: true, totalMatched: 12345 } as never);
    renderViewer();

    await screen.findAllByTestId('audit-row');
    await user.click(screen.getByTestId('audit-export-csv'));
    await waitFor(() => {
      expect(toast.warning).toHaveBeenCalledWith(
        'admin.audit.export_truncated',
      );
    });
  });

  it('does not warn for a complete export', async () => {
    const user = userEvent.setup();
    vi.mocked(exportAuditEvents).mockResolvedValue({ truncated: false, totalMatched: 52 } as never);
    renderViewer();

    await screen.findAllByTestId('audit-row');
    await user.click(screen.getByTestId('audit-export-json'));
    await waitFor(() => {
      expect(exportAuditEvents).toHaveBeenCalledTimes(1);
    });
    expect(toast.warning).not.toHaveBeenCalled();
  });
});
