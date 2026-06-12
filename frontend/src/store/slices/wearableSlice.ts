import { create } from 'zustand';

interface WearableData {
  timestamp: string;
  heart_rate?: number;
  steps?: number;
  calories?: number;
  distance?: number;
}

interface WearableState {
  data: WearableData[];
  summary: {
    steps: number;
    calories: number;
    heart_rate: {
      min: number;
      max: number;
      avg: number;
    };
  } | null;
  
  setData: (data: WearableData[]) => void;
  setSummary: (summary: WearableState['summary']) => void;
}

export const useWearableStore = create<WearableState>((set) => ({
  data: [],
  summary: null,
  
  setData: (data) => set({ data }),
  setSummary: (summary) => set({ summary })
}));