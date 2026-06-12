import { create } from 'zustand';

interface ConfirmationModalOptions {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmVariant?: 'danger' | 'primary';
  onConfirm: () => void | Promise<void>;
}

interface BreadcrumbItem {
  label: string;
  path?: string;
  icon?: React.ReactNode;
}

interface PageHeaderConfig {
  instanceId: string;
  title: string;
  subtitle?: string | React.ReactNode;
  icon?: React.ReactNode;
  details?: React.ReactNode;
  actions?: React.ReactNode;
  center?: React.ReactNode;
  breadcrumbs?: BreadcrumbItem[];
  showBackButton?: boolean;
  sticky?: boolean;
}

interface UIState {
  confirmationModal: ConfirmationModalOptions | null;
  sidebarOpen: boolean;
  sidebarCollapsed: boolean;
  aiDrawerOpen: boolean;
  
  // Search properties
  isSearchLauncherOpen: boolean;
  searchMode: 'page' | 'global';
  pageSearchTerm: string;
  isPageSearchSupported: boolean;

  currentAiSessionId: string | null;
  lastNonAiPath: string;
  currentExaminationId: string | null;
  currentBiomarkerId: string | null;
  currentMedicationId: string | null;
  pendingAIMessage: string | null;
  pageHeaderConfig: PageHeaderConfig | null;

  showConfirmation: (options: ConfirmationModalOptions) => void;
  hideConfirmation: () => void;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebarCollapse: () => void;
  toggleAIDrawer: () => void;
  setAIDrawerOpen: (open: boolean) => void;
  
  setSearchLauncherOpen: (open: boolean) => void;
  toggleSearchLauncher: () => void;
  setSearchMode: (mode: 'page' | 'global') => void;
  setPageSearchTerm: (term: string) => void;
  setIsPageSearchSupported: (supported: boolean) => void;

  setCurrentAiSessionId: (id: string | null) => void;
  setLastNonAiPath: (path: string) => void;
  setCurrentExaminationId: (id: string | null) => void;
  setCurrentBiomarkerId: (id: string | null) => void;
  setCurrentMedicationId: (id: string | null) => void;
  setPendingAIMessage: (message: string | null) => void;
  setPageHeaderConfig: (config: PageHeaderConfig | null) => void;
}

export const useUIStore = create<UIState>((set) => ({
  confirmationModal: null,
  sidebarOpen: false,
  sidebarCollapsed: false,
  aiDrawerOpen: false,

  isSearchLauncherOpen: false,
  searchMode: 'page',
  pageSearchTerm: '',
  isPageSearchSupported: false,

  currentAiSessionId: null,
  lastNonAiPath: '/',
  currentExaminationId: null,
  currentBiomarkerId: null,
  currentMedicationId: null,
  pendingAIMessage: null,
  pageHeaderConfig: null,

  showConfirmation: (options) => set({ confirmationModal: options }),
  hideConfirmation: () => set({ confirmationModal: null }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
  toggleSidebarCollapse: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  toggleAIDrawer: () => set((state) => ({ aiDrawerOpen: !state.aiDrawerOpen })),
  setAIDrawerOpen: (open) => set({ aiDrawerOpen: open }),

  setSearchLauncherOpen: (open) => set({ isSearchLauncherOpen: open }),
  toggleSearchLauncher: () => set((state) => ({ isSearchLauncherOpen: !state.isSearchLauncherOpen })),
  setSearchMode: (mode) => set({ searchMode: mode }),
  setPageSearchTerm: (term) => set({ pageSearchTerm: term }),
  setIsPageSearchSupported: (supported) => set({ isPageSearchSupported: supported }),

  setCurrentAiSessionId: (id) => set({ currentAiSessionId: id }),
  setLastNonAiPath: (path) => set({ lastNonAiPath: path }),
  setCurrentExaminationId: (id) => set({ currentExaminationId: id }),
  setCurrentBiomarkerId: (id) => set({ currentBiomarkerId: id }),
  setCurrentMedicationId: (id) => set({ currentMedicationId: id }),
  setPendingAIMessage: (message) => set({ pendingAIMessage: message }),
  setPageHeaderConfig: (config) => set({ pageHeaderConfig: config }),
}));
