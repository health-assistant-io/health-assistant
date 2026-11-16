import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { RequirePermission } from './RequirePermission';
import { useAccessStore } from '../../store/slices/accessSlice';

function setAccess(permissions: string[], isHydrated = true) {
  useAccessStore.setState({
    permissions: new Set(permissions),
    roles: [],
    tenant: null,
    patientGrants: [],
    pendingGrants: [],
    isHydrated,
    hasError: false,
  });
}

function renderGuard(permission: string | string[]) {
  return render(
    <MemoryRouter initialEntries={['/admin/tenant/users']}>
      <Routes>
        <Route
          path="/admin/tenant/users"
          element={
            <RequirePermission permission={permission}>
              <div data-testid="guarded" />
            </RequirePermission>
          }
        />
        <Route path="/no-access" element={<div data-testid="no-access" />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('RequirePermission (ADR 0054 UX guard)', () => {
  beforeEach(() => {
    setAccess([], false);
  });

  it('renders children when the mirror holds the permission', () => {
    setAccess(['user:read', 'patient:read']);
    renderGuard('user:read');
    expect(screen.getByTestId('guarded')).toBeInTheDocument();
    expect(screen.queryByTestId('no-access')).not.toBeInTheDocument();
  });

  it('accepts any-of permission lists', () => {
    setAccess(['catalog:read']);
    const first = renderGuard(['catalog:read', 'seed_source:read']);
    expect(screen.getByTestId('guarded')).toBeInTheDocument();
    first.unmount();

    setAccess(['settings:read']);
    renderGuard(['catalog:read', 'seed_source:read']);
    expect(screen.getByTestId('no-access')).toBeInTheDocument();
  });

  it('redirects to /no-access when the permission is not held', () => {
    setAccess(['patient:read']);
    renderGuard('user:read');
    expect(screen.getByTestId('no-access')).toBeInTheDocument();
    expect(screen.queryByTestId('guarded')).not.toBeInTheDocument();
  });

  it('waits while the mirror is unhydrated (no flash of no-access)', () => {
    setAccess(['user:read'], false);
    renderGuard('user:read');
    expect(screen.queryByTestId('guarded')).not.toBeInTheDocument();
    expect(screen.queryByTestId('no-access')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('fails closed on an unhydrated empty mirror', () => {
    setAccess([], true);
    renderGuard('user:read');
    expect(screen.getByTestId('no-access')).toBeInTheDocument();
  });
});
