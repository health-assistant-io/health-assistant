import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('react-i18next', () => {
  const t = (key: string, opts?: unknown) => {
    if (typeof opts === 'string') return opts;
    if (opts && typeof opts === 'object' && 'defaultValue' in opts) {
      return String((opts as { defaultValue: string }).defaultValue);
    }
    return key;
  };
  return { useTranslation: () => ({ t, i18n: { language: 'en' } }) };
});

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const { showConfirmation } = vi.hoisted(() => ({
  showConfirmation: vi.fn((options: { onConfirm: () => void }) => options.onConfirm()),
}));
vi.mock('../../../store/slices/uiSlice', () => {
  const state = { showConfirmation, setPageHeaderConfig: vi.fn(), pageHeaderConfig: null };
  const useUIStore = (selector?: (value: typeof state) => unknown) =>
    selector ? selector(state) : state;
  useUIStore.getState = () => state;
  return { useUIStore };
});

vi.mock('../../../services/ssoService', () => ({
  listSsoConfigs: vi.fn(),
  createSsoConfig: vi.fn(),
  updateSsoConfig: vi.fn(),
  deleteSsoConfig: vi.fn(),
  testSsoConnection: vi.fn(),
  listScimTokens: vi.fn(),
  createScimToken: vi.fn(),
  revokeScimToken: vi.fn(),
}));

import { toast } from 'react-toastify';
import EnterpriseIdentity from '../EnterpriseIdentity';
import {
  createScimToken,
  createSsoConfig,
  listScimTokens,
  listSsoConfigs,
  testSsoConnection,
} from '../../../services/ssoService';
import { useAccessStore } from '../../../store/slices/accessSlice';

const listSsoMock = listSsoConfigs as unknown as Mock;
const createSsoMock = createSsoConfig as unknown as Mock;
const testSsoMock = testSsoConnection as unknown as Mock;
const listScimMock = listScimTokens as unknown as Mock;
const createScimMock = createScimToken as unknown as Mock;

const CONFIG = {
  id: 'c1',
  tenant_id: 't1',
  issuer: 'https://login.clinic.example',
  client_id: 'health-assistant',
  client_secret_ref: 'tenant-sso/clinic',
  allowed_domains: ['clinic.example'],
  default_role: 'USER',
  group_role_map: { 'clinic-admins': 'ADMIN' },
  enabled: true,
  jit_enabled: true,
  scopes: ['openid', 'email'],
  created_at: '2026-11-01T10:00:00Z',
  updated_at: '2026-11-02T10:00:00Z',
};

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

describe('EnterpriseIdentity (program 12B UI)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedAccess(['settings:read', 'settings:write', 'tenant:manage']);
    listSsoMock.mockResolvedValue([CONFIG]);
    listScimMock.mockResolvedValue([]);
  });

  it('renders the tenant bindings without ever showing a secret value', async () => {
    render(<EnterpriseIdentity />);

    expect(await screen.findByText('https://login.clinic.example')).toBeInTheDocument();
    expect(screen.getByText(/tenant-sso\/clinic/)).toBeInTheDocument();
    expect(screen.getByText(/Domains: clinic\.example/)).toBeInTheDocument();
    expect(screen.getByText('Enabled')).toBeInTheDocument();
    expect(await screen.findByTestId('scim-empty')).toBeInTheDocument();
  });

  it('runs the connection probe and renders both checks', async () => {
    testSsoMock.mockResolvedValue({
      secret_resolved: false,
      discovery_ok: true,
      authorization_endpoint: 'https://login.clinic.example/authorize',
      token_endpoint: 'https://login.clinic.example/token',
      error: 'secret slot missing',
    });
    const user = userEvent.setup();
    render(<EnterpriseIdentity />);

    await user.click(await screen.findByTestId('sso-test-c1'));

    const result = await screen.findByTestId('sso-test-result-c1');
    expect(result).toHaveTextContent('Discovery');
    expect(result).toHaveTextContent('Secret slot');
    expect(result).toHaveTextContent('secret slot missing');
  });

  it('creates a provider from the form', async () => {
    createSsoMock.mockResolvedValue(CONFIG);
    const user = userEvent.setup();
    render(<EnterpriseIdentity />);

    await user.click(await screen.findByTestId('sso-add'));
    await user.type(screen.getByLabelText('Issuer URL'), 'https://idp.example');
    await user.type(screen.getByLabelText('Client ID'), 'client');
    await user.type(screen.getByLabelText('Secret slot name'), 'slot/name');
    await user.type(screen.getByLabelText('Allowed email domains'), 'idp.example');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(createSsoMock).toHaveBeenCalledWith(
        't1',
        expect.objectContaining({
          issuer: 'https://idp.example',
          client_id: 'client',
          client_secret_ref: 'slot/name',
          allowed_domains: ['idp.example'],
          default_role: 'USER',
          enabled: false,
        }),
      ),
    );
    expect(toast.success).toHaveBeenCalled();
  });

  it('creates a SCIM token and reveals the secret exactly once', async () => {
    createScimMock.mockResolvedValue({
      id: 'scim1',
      name: 'Okta',
      scopes: ['users:read', 'users:write'],
      active: true,
      last_used_at: null,
      expires_at: null,
      created_at: '2026-11-11T10:00:00Z',
      token: 'ha_scim_one_time_secret',
    });
    const user = userEvent.setup();
    render(<EnterpriseIdentity />);

    await user.click(await screen.findByTestId('scim-add'));
    await user.type(screen.getByLabelText('Token name'), 'Okta');
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() =>
      expect(createScimMock).toHaveBeenCalledWith('t1', { name: 'Okta', expires_at: null }),
    );
    expect(await screen.findByText('ha_scim_one_time_secret')).toBeInTheDocument();
    const secretBlock = screen.getByTestId('scim-created');
    expect(secretBlock).toHaveAttribute('data-as', 'secret-once-block');
    expect(
      screen.getByText('Copy this token now. It is shown once and cannot be recovered.'),
    ).toBeInTheDocument();

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    await user.click(within(secretBlock).getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith('ha_scim_one_time_secret');
    expect(await within(secretBlock).findByRole('button', { name: 'Copied' })).toBeInTheDocument();
  });

  it('hides every write affordance without the write permissions', async () => {
    seedAccess(['settings:read']);
    render(<EnterpriseIdentity />);

    expect(await screen.findByTestId('sso-config-card')).toBeInTheDocument();
    expect(screen.queryByTestId('sso-add')).not.toBeInTheDocument();
    expect(screen.queryByTestId('scim-add')).not.toBeInTheDocument();
  });
});
