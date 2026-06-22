import { describe, it, expect, vi, beforeEach } from 'vitest';
import api from '../api/axios';
import {
  listTenants, createTenant, updateTenant, hardDeleteTenant,
  switchIntoTenant, updateTenantUser,
} from './tenantService';

describe('tenantService (admin surface)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('listTenants sends paginated GET to /admin/tenants', async () => {
    api.get = vi.fn().mockResolvedValue({
      data: { items: [{ id: 't1', name: 'Acme' }], total: 1 },
    }) as any;

    const result = await listTenants({ search: 'acme', limit: 10, offset: 20 });

    expect(api.get).toHaveBeenCalledWith(
      '/admin/tenants?search=acme&limit=10&offset=20'
    );
    expect(result.total).toBe(1);
    expect(result.items[0].name).toBe('Acme');
  });

  it('createTenant sends a JSON body (not query params)', async () => {
    api.post = vi.fn().mockResolvedValue({ data: { id: 't1' } }) as any;

    await createTenant({ name: 'Acme', slug: 'acme', settings: { x: 1 } });

    expect(api.post).toHaveBeenCalledWith('/admin/tenants', {
      name: 'Acme',
      slug: 'acme',
      settings: { x: 1 },
    });
  });

  it('updateTenant sends PATCH with partial body', async () => {
    api.patch = vi.fn().mockResolvedValue({ data: {} }) as any;

    await updateTenant('t1', { name: 'New Name' });

    expect(api.patch).toHaveBeenCalledWith('/admin/tenants/t1', { name: 'New Name' });
  });

  it('hardDeleteTenant sends DELETE with confirmation body', async () => {
    api.request = vi.fn().mockResolvedValue({ data: { message: 'ok' } }) as any;

    const result = await hardDeleteTenant('t1', 'Acme');

    expect(api.request).toHaveBeenCalledWith({
      method: 'DELETE',
      url: '/admin/tenants/t1',
      data: { permanent: true, confirm_name: 'Acme' },
    });
    expect(result.message).toBe('ok');
  });

  it('switchIntoTenant posts to /switch', async () => {
    api.post = vi.fn().mockResolvedValue({
      data: {
        access_token: 'acc',
        refresh_token: 'ref',
        token_type: 'bearer',
        expires_in: 3600,
        scoped_tenant_id: 't1',
        original_tenant_id: 't0',
        tenant: { id: 't1', name: 'Acme' },
      },
    }) as any;

    const result = await switchIntoTenant('t1');
    expect(api.post).toHaveBeenCalledWith('/admin/tenants/t1/switch');
    expect(result.access_token).toBe('acc');
    expect(result.tenant.name).toBe('Acme');
  });

  it('updateTenantUser sends PATCH to user route', async () => {
    api.patch = vi.fn().mockResolvedValue({ data: {} }) as any;

    await updateTenantUser('t1', 'u1', { role: 'ADMIN' });

    expect(api.patch).toHaveBeenCalledWith('/admin/tenants/t1/users/u1', { role: 'ADMIN' });
  });
});
