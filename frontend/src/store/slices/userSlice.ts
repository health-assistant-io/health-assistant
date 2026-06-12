import { create } from 'zustand';

interface UserSliceState {
  user: {
    id: string;
    email: string;
    role: string;
    tenant_id: string;
    settings: {
      preferred_units: {
        weight: string;
        height: string;
        glucose: string;
      };
    };
  };
  
  setUser: (user: UserSliceState['user']) => void;
  updateSettings: (settings: UserSliceState['user']['settings']) => void;
}

export const useUserSlice = create<UserSliceState>((set) => ({
  user: {
    id: '',
    email: '',
    role: '',
    tenant_id: '',
    settings: {
      preferred_units: {
        weight: 'kg',
        height: 'cm',
        glucose: 'mmol/L'
      }
    }
  },
  
  setUser: (user) => set({ user }),
  
  updateSettings: (settings) => set((state) => ({
    user: { ...state.user, settings }
  }))
}));