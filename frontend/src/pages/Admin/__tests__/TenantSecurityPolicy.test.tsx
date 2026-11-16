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
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const POLICY = {
  tenant_id: 't1',
  mfa_required_roles: ['ADMIN'],
  system_admin_mfa_required: true,
  session_idle_minutes: null,
  session_max_hours: 720,
  concurrent_session_limit: null,
  ip_allowlist: ['203.0.113.0/24'],
  patient_sharing: true,
  manager_scope: 'tenant',
  updated_at: '2026-11-09T10:00:00Z',
};

vi.mock('../../../services/securityPolicyService', () => ({
  POLICY_MFA_ROLES: ['ADMIN', 'MANAGER', 'USER'],
  getSecurityPolicy: vi.fn(),
  updateSecurityPolicy: vi.fn(),
}));

import { toast } from 'react-toastify';
import {
  getSecurityPolicy,
  updateSecurityPolicy,
} from '../../../services/securityPolicyService';
import { useAccessStore } from '../../../store/slices/accessSlice';
import { useUIStore } from '../../../store/slices/uiSlice';
import { TenantSecurityPolicyCard, isValidCidr } from '../TenantSecurityPolicy';

function seedAccess(permissions: string[]) {
  useAccessStore.setState({
    permissions: new Set(permissions),
    roles: ['ADMIN'],
    tenant: { id: 't1', name: 'Clinic' },
    patientGrants: [],
    pendingGrants: [],
    isHydrated: true,
    hasError: false,
  });
}

function renderCard(canWrite: boolean) {
  return render(
    <MemoryRouter>
      <TenantSecurityPolicyCard tenantId="t1" canWrite={canWrite} />
    </MemoryRouter>
  );
}

describe('TenantSecurityPolicyCard (ADR 0054 phase 3 UI)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSecurityPolicy).mockResolvedValue(POLICY as never);
    seedAccess(['settings:read', 'settings:write']);
    useUIStore.setState({ confirmationModal: null });
  });

  it('renders every knob from the loaded policy, SYSTEM_ADMIN always-on', async () => {
    renderCard(true);

    expect(await screen.findByTestId('security-policy-card')).toBeInTheDocument();
    expect(screen.getByTestId('policy-mfa-ADMIN')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('policy-mfa-MANAGER')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('policy-mfa-system-admin')).toHaveTextContent('SYSTEM_ADMIN');
    expect(screen.getByTestId('policy-ip-list')).toHaveTextContent('203.0.113.0/24');
    expect(screen.getByLabelText('admin.policy.idle_label')).toHaveValue(null);
    expect(screen.getByLabelText('admin.policy.max_age_label')).toHaveValue(720);
    expect(screen.getByTestId('policy-scope-tenant')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('policy-share-on')).toHaveAttribute('aria-pressed', 'true');
  });

  it('saves a partial patch of exactly the changed keys', async () => {
    const user = userEvent.setup();
    vi.mocked(updateSecurityPolicy).mockResolvedValue(POLICY as never);
    renderCard(true);

    await screen.findByTestId('security-policy-card');
    await user.click(screen.getByTestId('policy-mfa-MANAGER'));
    await user.click(screen.getByTestId('policy-save'));

    await waitFor(() => {
      expect(updateSecurityPolicy).toHaveBeenCalledWith('t1', { mfa_required_roles: ['ADMIN', 'MANAGER'] });
    });
    expect(toast.success).toHaveBeenCalledWith('admin.policy.saved_toast');
  });

  it('sends an explicit null when a nullable knob is cleared', async () => {
    const user = userEvent.setup();
    vi.mocked(updateSecurityPolicy).mockResolvedValue(POLICY as never);
    renderCard(true);

    await screen.findByTestId('security-policy-card');
    await user.clear(screen.getByLabelText('admin.policy.max_age_label'));
    await user.click(screen.getByTestId('policy-save'));

    await waitFor(() => {
      expect(updateSecurityPolicy).toHaveBeenCalledWith('t1', { session_max_hours: null });
    });
  });

  it('surfaces 422 validation errors as field errors', async () => {
    const user = userEvent.setup();
    vi.mocked(updateSecurityPolicy).mockRejectedValue({
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'session_idle_minutes'], msg: 'Input should be greater than or equal to 5', type: 'greater_than_equal' },
          ],
        },
      },
    });
    renderCard(true);

    await screen.findByTestId('security-policy-card');
    await user.type(screen.getByLabelText('admin.policy.idle_label'), '2');
    await user.click(screen.getByTestId('policy-save'));

    await waitFor(() => {
      expect(screen.getByTestId('policy-error-banner')).toHaveTextContent(
        'session_idle_minutes: Input should be greater than or equal to 5'
      );
    });
    expect(screen.getByTestId('policy-field-session_idle_minutes')).toHaveTextContent(
      'Input should be greater than or equal to 5'
    );
    expect(toast.error).toHaveBeenCalledWith('admin.policy.validation_toast');
  });

  it('toasts a plain message when the write is denied (403)', async () => {
    const user = userEvent.setup();
    vi.mocked(updateSecurityPolicy).mockRejectedValue({
      response: { status: 403, data: { detail: 'Not authorized' } },
    });
    renderCard(true);

    await screen.findByTestId('security-policy-card');
    await user.click(screen.getByTestId('policy-mfa-USER'));
    await user.click(screen.getByTestId('policy-save'));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Not authorized');
    });
    expect(screen.queryByTestId('policy-error-banner')).not.toBeInTheDocument();
  });

  it('is read-only without settings:write', async () => {
    renderCard(false);

    await screen.findByTestId('security-policy-card');
    expect(screen.getByText('admin.policy.read_only_badge')).toBeInTheDocument();
    expect(screen.queryByTestId('policy-save')).not.toBeInTheDocument();
    expect(screen.getByTestId('policy-mfa-ADMIN')).toBeDisabled();
    expect(screen.queryByTestId('policy-ip-add')).not.toBeInTheDocument();
    expect(screen.getByTestId('policy-ip-remove-203.0.113.0/24')).toBeDisabled();
  });

  it('adds a CIDR after validation and removes one through the confirmation modal', async () => {
    const user = userEvent.setup();
    renderCard(true);

    await screen.findByTestId('security-policy-card');

    await user.type(screen.getByTestId('policy-ip-input'), 'not-a-cidr');
    await user.click(screen.getByTestId('policy-ip-add'));
    expect(screen.getByText('admin.policy.cidr_invalid')).toBeInTheDocument();
    expect(screen.getByTestId('policy-ip-list')).toHaveTextContent('203.0.113.0/24');

    await user.clear(screen.getByTestId('policy-ip-input'));
    await user.type(screen.getByTestId('policy-ip-input'), '198.51.100.7/32');
    await user.click(screen.getByTestId('policy-ip-add'));
    expect(screen.getByTestId('policy-ip-list')).toHaveTextContent('198.51.100.7/32');

    await user.click(screen.getByTestId('policy-ip-remove-198.51.100.7/32'));
    const modal = useUIStore.getState().confirmationModal;
    expect(modal).not.toBeNull();
    await modal!.onConfirm();
    expect(screen.getByTestId('policy-ip-list')).not.toHaveTextContent('198.51.100.7/32');
  });

  it('shows a load error state when the read fails', async () => {
    vi.mocked(getSecurityPolicy).mockRejectedValue({
      response: { status: 404, data: { detail: 'Tenant not found' } },
    });
    renderCard(true);

    expect(await screen.findByTestId('policy-load-error')).toHaveTextContent('Tenant not found');
  });
});

describe('isValidCidr', () => {
  it('accepts IPv4 and IPv6 CIDR ranges and bare addresses', () => {
    expect(isValidCidr('203.0.113.0/24')).toBe(true);
    expect(isValidCidr('198.51.100.7')).toBe(true);
    expect(isValidCidr('2001:db8::/32')).toBe(true);
    expect(isValidCidr('10.0.0.1/33')).toBe(false);
    expect(isValidCidr('999.1.1.1/8')).toBe(false);
    expect(isValidCidr('not-a-cidr')).toBe(false);
    expect(isValidCidr('10.0.0.0/8/8')).toBe(false);
  });
});
