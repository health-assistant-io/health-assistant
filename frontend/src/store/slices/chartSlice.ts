import { create } from 'zustand';

interface ChartState {
  selectedBiomarker: string;
  dateRange: {
    start: string;
    end: string;
  };
  showReferenceRanges: boolean;
  
  setSelectedBiomarker: (biomarker: string) => void;
  setDateRange: (range: { start: string; end: string }) => void;
  setShowReferenceRanges: (show: boolean) => void;
}

export const useChartStore = create<ChartState>((set) => ({
  selectedBiomarker: 'glucose',
  dateRange: {
    start: '',
    end: ''
  },
  showReferenceRanges: true,
  
  setSelectedBiomarker: (biomarker) => set({ selectedBiomarker: biomarker }),
  setDateRange: (range) => set({ dateRange: range }),
  setShowReferenceRanges: (show) => set({ showReferenceRanges: show })
}));