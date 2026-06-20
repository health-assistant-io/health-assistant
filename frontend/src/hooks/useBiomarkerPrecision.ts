import { useSettingsStore } from '../store/slices/settingsSlice';
import type { BiomarkerPrecisionProfile } from '../utils/biomarkerUtils';

export const useBiomarkerPrecisionProfile = (): BiomarkerPrecisionProfile => {
  const def = useSettingsStore(state => state.biomarkerPrecision);
  const below30 = useSettingsStore(state => state.precisionBelow30);
  const below10 = useSettingsStore(state => state.precisionBelow10);
  const below3 = useSettingsStore(state => state.precisionBelow3);
  const below1 = useSettingsStore(state => state.precisionBelow1);
  return { default: def, below_30: below30, below_10: below10, below_3: below3, below_1: below1 };
};
