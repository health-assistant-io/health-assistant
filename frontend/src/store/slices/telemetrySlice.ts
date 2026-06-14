import { create } from 'zustand';

interface TelemetryData {
  timestamp: string;
  heart_rate?: number;
  steps?: number;
  calories?: number;
  distance?: number;
}

interface TelemetryState {
  data: TelemetryData[];
  summary: {
    steps: number;
    calories: number;
    heart_rate: {
      min: number;
      max: number;
      avg: number;
    };
  } | null;
  
  setData: (data: TelemetryData[]) => void;
  setSummary: (summary: TelemetryState['summary']) => void;
}

export const useTelemetryStore = create<TelemetryState>((set) => ({
  data: [],
  summary: null,
  
  setData: (data) => set({ data }),
  setSummary: (summary) => set({ summary })
}));