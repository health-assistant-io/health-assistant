import { create } from 'zustand';

interface DashboardState {
  recentDocuments: Array<{
    id: string;
    filename: string;
    created_at: string;
  }>;
  upcomingAppointments: Array<{
    id: string;
    title: string;
    date: string;
  }>;
  alerts: Array<{
    type: string;
    message: string;
    timestamp: string;
  }>;
  summary: {
    totalDocuments: number;
    totalObservations: number;
    lastUpload: string;
  };
  latestExamination: any | null;
  latestImaging: any[];
  latestLabs: any[];
  activeLayout: any | null;
  layoutsList: any[];
  
  setDashboardData: (data: Partial<DashboardState>) => void;
  setActiveLayout: (layout: any) => void;
  setLayoutsList: (layouts: any[]) => void;
}

export const useDashboardStore = create<DashboardState>((set) => ({
  recentDocuments: [],
  upcomingAppointments: [],
  alerts: [],
  summary: {
    totalDocuments: 0,
    totalObservations: 0,
    lastUpload: ''
  },
  latestExamination: null,
  latestImaging: [],
  latestLabs: [],
  activeLayout: null,
  layoutsList: [],
  
  setDashboardData: (data) => set(data),
  setActiveLayout: (layout) => set({ activeLayout: layout }),
  setLayoutsList: (layouts) => set({ layoutsList: layouts })
}));
