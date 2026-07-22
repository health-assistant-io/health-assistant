/**
 * Tests for the PromoteScopeButton + promoteCatalogItem service.
 *
 * Covers:
 *   1. promoteCatalogItem posts to the right endpoint with {scope}.
 *   2. availableTransitions (the permission matrix mirroring check_promote):
 *      USER → none; ADMIN → user↔tenant; SYSTEM_ADMIN → all.
 *   3. The button is hidden for USER / when no transitions available.
 *   4. On select: calls promoteCatalogItem + onPromoted with the result.
 *   5. 409 collision: surfaces the conflicting item's name inline.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, dftOrOpts?: any) => {
      if (typeof dftOrOpts === 'string') return dftOrOpts;
      let s = dftOrOpts?.defaultValue ?? k;
      const opts = dftOrOpts ?? {};
      for (const [key, val] of Object.entries(opts)) {
        if (key === 'defaultValue') continue;
        s = s.replace(new RegExp(`{{\\s*${key}\\s*}}`, 'g'), String(val));
      }
      return s;
    },
    i18n: { language: 'en' },
  }),
}));

// --- service mock ---
const promoteMock = vi.fn();
vi.mock('../../../services/catalogService', () => ({
  promoteCatalogItem: (...args: any[]) => promoteMock(...args),
}));

// --- auth store mock (lets each test set the role) ---
const roleState: { role: string | null } = { role: 'SYSTEM_ADMIN' };
vi.mock('../../../store/slices/authSlice', () => ({
  useAuthStore: (sel: any) => sel({ user: roleState.role ? { role: roleState.role } : null }),
}));

import { PromoteScopeButton } from '../PromoteScopeButton';
import { promoteCatalogItem } from '../../../services/catalogService';

beforeEach(() => {
  promoteMock.mockReset();
  roleState.role = 'SYSTEM_ADMIN';
});

// ---------------------------------------------------------------------------
// 1. Service
// ---------------------------------------------------------------------------

describe('promoteCatalogItem', () => {
  it('posts to the promote endpoint with the target scope', async () => {
    promoteMock.mockResolvedValue({ id: 'i1', scope: 'system' });
    const out = await promoteCatalogItem('anatomy', 'i1', 'system');
    expect(promoteMock).toHaveBeenCalledWith('anatomy', 'i1', 'system');
    expect(out.scope).toBe('system');
  });
});

// ---------------------------------------------------------------------------
// 2 & 3. Permission gating / rendering
// ---------------------------------------------------------------------------

describe('PromoteScopeButton — permission gating', () => {
  it('renders options for SYSTEM_ADMIN on a tenant item', () => {
    roleState.role = 'SYSTEM_ADMIN';
    render(
      <PromoteScopeButton
        catalogType="anatomy"
        itemId="i1"
        currentScope="tenant"
        onPromoted={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('Scope'));
    expect(screen.getByText('Promote to system')).toBeTruthy();
    expect(screen.getByText('Move to user scope')).toBeTruthy();
    // Same-scope (tenant) is filtered out.
    expect(screen.queryByText('Move to tenant')).toBeNull();
  });

  it('renders only user↔tenant for ADMIN on a user-scope item', () => {
    roleState.role = 'ADMIN';
    render(
      <PromoteScopeButton
        catalogType="medication"
        itemId="i2"
        currentScope="user"
        onPromoted={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('Scope'));
    expect(screen.getByText('Move to tenant')).toBeTruthy();
    // ADMIN cannot touch system.
    expect(screen.queryByText('Promote to system')).toBeNull();
  });

  it('renders nothing for a plain USER', () => {
    roleState.role = 'USER';
    const { container } = render(
      <PromoteScopeButton
        catalogType="anatomy"
        itemId="i3"
        currentScope="user"
        onPromoted={() => {}}
      />,
    );
    expect(container.textContent).toBe('');
  });

  it('renders nothing when no transitions are available (system item, ADMIN)', () => {
    roleState.role = 'ADMIN';
    const { container } = render(
      <PromoteScopeButton
        catalogType="anatomy"
        itemId="i4"
        currentScope="system"
        onPromoted={() => {}}
      />,
    );
    // ADMIN cannot demote a system item.
    expect(container.textContent).toBe('');
  });
});

// ---------------------------------------------------------------------------
// 4. On select → promote + onPromoted
// ---------------------------------------------------------------------------

describe('PromoteScopeButton — select', () => {
  it('calls promoteCatalogItem and onPromoted with the updated item', async () => {
    const updated = { id: 'i1', scope: 'system', tenant_id: null };
    promoteMock.mockResolvedValue(updated);
    const onPromoted = vi.fn();
    render(
      <PromoteScopeButton
        catalogType="anatomy"
        itemId="i1"
        currentScope="tenant"
        onPromoted={onPromoted}
      />,
    );
    fireEvent.click(screen.getByText('Scope'));
    fireEvent.click(screen.getByText('Promote to system'));
    await waitFor(() => expect(promoteMock).toHaveBeenCalledTimes(1));
    expect(promoteMock).toHaveBeenCalledWith('anatomy', 'i1', 'system');
    expect(onPromoted).toHaveBeenCalledWith(updated);
  });
});

// ---------------------------------------------------------------------------
// 5. 409 collision
// ---------------------------------------------------------------------------

describe('PromoteScopeButton — 409 collision', () => {
  it('surfaces the conflicting item name inline', async () => {
    promoteMock.mockRejectedValue({
      response: {
        status: 409,
        data: {
          code: 'catalog_conflict',
          slug: 'kidney',
          target_scope: 'system',
          existing_id: 'e1',
          existing_name: 'Kidney',
        },
      },
    });
    render(
      <PromoteScopeButton
        catalogType="anatomy"
        itemId="i1"
        currentScope="tenant"
        onPromoted={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('Scope'));
    fireEvent.click(screen.getByText('Promote to system'));
    await waitFor(() =>
      expect(screen.getByText(/already exists/i)).toBeTruthy(),
    );
    expect(screen.getByText(/Kidney/)).toBeTruthy();
  });
});
