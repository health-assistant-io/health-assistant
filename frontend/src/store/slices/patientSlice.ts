import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface Patient {
  id: string;
  name: {
    family: string;
    given: string[];
  };
  gender: string;
  birthDate: string;
  mrn?: string;
  dashboard_layout?: any;
}

interface PatientState {
  patients: Patient[];
  currentPatient: Patient | null;
  
  setPatients: (patients: Patient[]) => void;
  setCurrentPatient: (patient: Patient | null) => void;
  addPatient: (patient: Patient) => void;
  clearPatientContext: () => void;
}

export const usePatientStore = create<PatientState>()(
  persist(
    (set) => ({
      patients: [],
      currentPatient: null,
      
      setPatients: (patients) => set({ patients }),
      setCurrentPatient: (patient) => set({ currentPatient: patient }),
      addPatient: (patient) => set((state) => ({
        patients: [...state.patients, patient]
      })),
      clearPatientContext: () => set({ patients: [], currentPatient: null })
    }),
    {
      name: 'patient-storage',
      partialize: (state) => ({ currentPatient: state.currentPatient }),
    }
  )
);