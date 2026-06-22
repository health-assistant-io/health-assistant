import { create } from 'zustand';
import {
  getMyTenant,
  listTenants,
  type Tenant,
} from '../../services/tenantService';
import { useAuthStore } from './authSlice';

interface TenantState {
  currentTenant: Tenant | null;
  tenants: Tenant[];
  isLoadingTenant: boolean;
  isLoadingList: boolean;
  loadCurrentTenant: () => Promise<void>;
  loadTenants: () => Promise<void>;
  setCurrentTenant: (tenant: Tenant | null) => void;
  clear: () => void;
}

export const useTenantStore = create<TenantState>((set, get) => ({
  currentTenant: null,
  tenants: [],
  isLoadingTenant: false,
  isLoadingList: false,

  loadCurrentTenant: async () => {
    const user = useAuthStore.getState().user;
    if (!user?.tenant_id) return;
    if (get().isLoadingTenant) return;
    set({ isLoadingTenant: true });
    try {
      const tenant = await getMyTenant();
      set({ currentTenant: tenant, isLoadingTenant: false });
    } catch (err) {
      console.error('Failed to load current tenant:', err);
      set({ isLoadingTenant: false });
    }
  },

  loadTenants: async () => {
    const user = useAuthStore.getState().user;
    if (user?.role !== 'SYSTEM_ADMIN') return;
    if (get().isLoadingList) return;
    set({ isLoadingList: true });
    try {
      const response = await listTenants({ limit: 200 });
      set({ tenants: response.items, isLoadingList: false });
    } catch (err) {
      console.error('Failed to load tenants list:', err);
      set({ isLoadingList: false });
    }
  },

  setCurrentTenant: (tenant) => set({ currentTenant: tenant }),
  clear: () => set({ currentTenant: null, tenants: [] }),
}));
