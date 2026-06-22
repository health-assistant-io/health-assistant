import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { TenantSwitcher } from './TenantSwitcher';
import { useAuthStore } from '../../store/slices/authSlice';
import { useTenantStore } from '../../store/slices/tenantSlice';
import { useTenantSwitchStore } from '../../store/slices/tenantSwitchSlice';

// Mock the services so no network calls happen
vi.mock('../../services/tenantService', () => ({
  getMyTenant: vi.fn().mockResolvedValue({ id: 't1', name: 'My Clinic', slug: 'my-clinic', is_active: true, settings: {} }),
  listTenants: vi.fn().mockResolvedValue({
    items: [
      { id: 't1', name: 'My Clinic', slug: 'my-clinic', is_active: true, settings: {} },
      { id: 't2', name: 'Other Org', slug: 'other-org', is_active: true, settings: {} },
      { id: 't3', name: 'Inactive Corp', slug: 'inactive-corp', is_active: false, settings: {} },
    ],
    total: 3,
  }),
  switchIntoTenant: vi.fn(),
  exitTenantSwitch: vi.fn(),
}));

// Mock react-toastify
vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({
    user: { id: 'u1', email: 'admin@test.com', role: 'SYSTEM_ADMIN', tenant_id: 't1', settings: {} },
    token: 'fake-token',
    refreshToken: 'fake-refresh',
    isAuthenticated: true,
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    updateUser: vi.fn(),
    initialize: vi.fn(),
  });
  useTenantStore.setState({
    currentTenant: { id: 't1', name: 'My Clinic', slug: 'my-clinic', is_active: true, settings: {} },
    tenants: [],
    isLoadingTenant: false,
    isLoadingList: false,
    loadCurrentTenant: vi.fn(),
    loadTenants: vi.fn(),
    setCurrentTenant: vi.fn(),
    clear: vi.fn(),
  });
  useTenantSwitchStore.setState({
    switched: false,
    originalTenantId: null,
    scopedTenant: null,
    pendingRestore: false,
    enterTenant: vi.fn(),
    exitTenant: vi.fn().mockResolvedValue(undefined),
    clear: vi.fn(),
  });
});

describe('TenantSwitcher', () => {
  it('renders "System Admin" for a non-switched SYSTEM_ADMIN', () => {
    renderWithRouter(<TenantSwitcher />);
    // SYSTEM_ADMIN is a global role — shows "System Admin", not a tenant name
    expect(screen.getByText('System Admin')).toBeInTheDocument();
  });

  it('shows a dropdown chevron for SYSTEM_ADMIN', () => {
    renderWithRouter(<TenantSwitcher />);
    // The chevron is an SVG with a lucide class
    const chevron = document.querySelector('.lucide-chevron-down');
    expect(chevron).toBeInTheDocument();
  });

  it('opens the dropdown on click and loads tenant list', async () => {
    const { loadTenants } = useTenantStore.getState();
    renderWithRouter(<TenantSwitcher />);
    // Click on the "System Admin" label to open the dropdown
    const trigger = screen.getByText('System Admin');
    fireEvent.click(trigger);
    expect(loadTenants).toHaveBeenCalled();
  });

  it('renders static tenant badge for non-admin users', () => {
    useAuthStore.setState({
      ...useAuthStore.getState(),
      user: { id: 'u2', email: 'user@test.com', role: 'USER', tenant_id: 't1', settings: {} },
    });
    renderWithRouter(<TenantSwitcher />);
    // Non-admin sees their tenant name, not "System Admin"
    expect(screen.getByText('My Clinic')).toBeInTheDocument();
    // Non-admin: no chevron (no dropdown)
    const chevron = document.querySelector('.lucide-chevron-down');
    expect(chevron).not.toBeInTheDocument();
  });

  it('shows tenant name + switched badge when in switched state', () => {
    useTenantSwitchStore.setState({
      ...useTenantSwitchStore.getState(),
      switched: true,
      scopedTenant: { id: 't2', name: 'Other Org', slug: 'other-org', is_active: true, settings: {} },
      originalTenantId: 't1',
    });
    renderWithRouter(<TenantSwitcher />);
    // When switched, shows the target tenant name
    expect(screen.getByText('Other Org')).toBeInTheDocument();
    expect(screen.getByText('Switched')).toBeInTheDocument();
  });
});
