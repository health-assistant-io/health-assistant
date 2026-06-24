import { useEffect, useState, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Responsive, WidthProvider } from 'react-grid-layout/legacy';
import { useDashboardStore } from '../../store/slices/dashboardSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useChartStore } from '../../store/slices/chartSlice';
import { useSettingsStore } from '../../store/slices/settingsSlice';
import { getDashboardData, getBiomarkerTrends, getCachedDashboardData, getCachedBiomarkerTrends } from '../../services/analyticsService';
import { getPatientMedications } from '../../services/medicationService';
import { getPatientAllergies } from '../../services/allergyService';
import { getExaminations } from '../../services/examinationService';
import { getPatientEvents } from '../../services/clinicalEventService';
import { 
  createPatientLayout, 
  updatePatientLayout,
  getPatientLayouts,
  deletePatientLayout
} from '../../services/dashboardLayoutService';
import { LoadingState } from '../../components/ui/LoadingState';
import { NoPatientState } from '../../components/ui/NoPatientState';
import { Settings, Plus, Save, ChevronDown, Trash2, Copy, LayoutTemplate, Edit2 } from 'lucide-react';
import {
  getCardDefinition,
  resolveCardComponent,
  resolveDefaultConfig,
  resolveDefaultLayout,
  ADDABLE_CARDS,
} from '../../components/dashboard/cardRegistry';
import { getBestIcon } from '../../components/dashboard';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

const ResponsiveGridLayout = WidthProvider(Responsive);

const DEFAULT_BIOMARKERS = ['Blood Pressure', 'Heart Rate', 'Body Temp', 'Blood Sugar'];

const DEFAULT_LAYOUT_CONFIG = {
  lg: [
    { i: 'bp', x: 0, y: 0, w: 3, h: 2 },
    { i: 'hr', x: 3, y: 0, w: 3, h: 2 },
    { i: 'temp', x: 6, y: 0, w: 3, h: 2 },
    { i: 'sugar', x: 9, y: 0, w: 3, h: 2 },
    { i: 'trends', x: 0, y: 2, w: 8, h: 4 },
    { i: 'imaging', x: 8, y: 2, w: 4, h: 5 },
    { i: 'examination', x: 8, y: 7, w: 4, h: 2 },
    { i: 'labs', x: 0, y: 6, w: 8, h: 5 }
  ],
  md: [
    { i: 'bp', x: 0, y: 0, w: 5, h: 2 },
    { i: 'hr', x: 5, y: 0, w: 5, h: 2 },
    { i: 'temp', x: 0, y: 2, w: 5, h: 2 },
    { i: 'sugar', x: 5, y: 2, w: 5, h: 2 },
    { i: 'trends', x: 0, y: 4, w: 10, h: 4 },
    { i: 'imaging', x: 0, y: 8, w: 10, h: 5 },
    { i: 'examination', x: 0, y: 13, w: 10, h: 3 },
    { i: 'labs', x: 0, y: 16, w: 10, h: 5 }
  ],
  sm: [
    { i: 'bp', x: 0, y: 0, w: 3, h: 2 },
    { i: 'hr', x: 3, y: 0, w: 3, h: 2 },
    { i: 'temp', x: 0, y: 2, w: 3, h: 2 },
    { i: 'sugar', x: 3, y: 2, w: 3, h: 2 },
    { i: 'trends', x: 0, y: 4, w: 6, h: 5 },
    { i: 'imaging', x: 0, y: 9, w: 6, h: 5 },
    { i: 'examination', x: 0, y: 14, w: 6, h: 3 },
    { i: 'labs', x: 0, y: 17, w: 6, h: 5 }
  ],
  xs: [
    { i: 'bp', x: 0, y: 0, w: 4, h: 2 },
    { i: 'hr', x: 0, y: 2, w: 4, h: 2 },
    { i: 'temp', x: 0, y: 4, w: 4, h: 2 },
    { i: 'sugar', x: 0, y: 6, w: 4, h: 2 },
    { i: 'trends', x: 0, y: 8, w: 4, h: 5 },
    { i: 'imaging', x: 0, y: 13, w: 4, h: 5 },
    { i: 'examination', x: 0, y: 18, w: 4, h: 3 },
    { i: 'labs', x: 0, y: 21, w: 4, h: 5 }
  ],
  xxs: [
    { i: 'bp', x: 0, y: 0, w: 2, h: 2 },
    { i: 'hr', x: 0, y: 2, w: 2, h: 2 },
    { i: 'temp', x: 0, y: 4, w: 2, h: 2 },
    { i: 'sugar', x: 0, y: 6, w: 2, h: 2 },
    { i: 'trends', x: 0, y: 8, w: 2, h: 6 },
    { i: 'imaging', x: 0, y: 14, w: 2, h: 5 },
    { i: 'examination', x: 0, y: 19, w: 2, h: 3 },
    { i: 'labs', x: 0, y: 22, w: 2, h: 6 }
  ]
};

const DEFAULT_CARDS = [
  { id: 'bp', type: 'biomarker', config: { biomarker: 'Blood Pressure', icon: 'Activity' } },
  { id: 'hr', type: 'biomarker', config: { biomarker: 'Heart Rate', icon: 'Heart' } },
  { id: 'temp', type: 'biomarker', config: { biomarker: 'Body Temp', icon: 'Thermometer' } },
  { id: 'sugar', type: 'biomarker', config: { biomarker: 'Blood Sugar', icon: 'Droplets' } },
  { id: 'trends', type: 'trends', config: { biomarker: 'Cholesterol (Total)' } },
  { id: 'imaging', type: 'imaging', config: {} },
  { id: 'examination', type: 'examination', config: {} },
  { id: 'labs', type: 'labs', config: {} },
];

interface BiomarkerOption {
  id: string | null;
  slug: string;
  name: string;
}

function Dashboard() {
  const { t } = useTranslation();
  const { 
    setDashboardData, 
    activeLayout, 
    setActiveLayout, 
    layoutsList, 
    setLayoutsList, 
    latestExamination, 
    latestImaging, 
    latestLabs,
    recentDocuments 
  } = useDashboardStore();
  const { currentPatient } = usePatientStore();
  const { selectedBiomarker, setSelectedBiomarker } = useChartStore();
  const theme = useSettingsStore(state => state.theme);
  
  const [availableBiomarkers, setAvailableBiomarkers] = useState<BiomarkerOption[]>([]);
  const [layouts, setLayouts] = useState<any>(DEFAULT_LAYOUT_CONFIG);
  const [cards, setCards] = useState<any[]>(DEFAULT_CARDS);
  const [cardsData, setCardsData] = useState<Record<string, any>>({});
  const [medications, setMedications] = useState<any[]>([]);
  const [allergies, setAllergies] = useState<any[]>([]);
  const [examinations, setExaminations] = useState<any[]>([]);
  const [clinicalEvents, setClinicalEvents] = useState<any[]>([]);
  const [isEditMode, setIsEditMode] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isLayoutMenuOpen, setIsLayoutMenuOpen] = useState(false);
  const [isAddCardMenuOpen, setIsAddCardMenuOpen] = useState(false);

  const loadAllCardData = useCallback(async () => {
    if (!currentPatient?.id) return;
    try {
      const biomarkerCards = cards.filter(c =>
        c.type === 'biomarker' || c.type === 'trends' || c.type === 'labs' ||
        c.type === 'range_gauge' || c.type === 'multi_biomarker_comparison' || c.type === 'health_summary'
      );

      const specificSlugs = new Set<string>();
      let fetchAll = false;

      biomarkerCards.forEach(c => {
        if (c.type === 'labs' || c.type === 'health_summary') {
          if (c.config.biomarkers && c.config.biomarkers.length > 0) {
            c.config.biomarkers.forEach((s: string) => specificSlugs.add(s));
          } else {
            fetchAll = true;
          }
        } else if (c.type === 'multi_biomarker_comparison') {
          if (c.config.biomarkers && c.config.biomarkers.length > 0) {
            c.config.biomarkers.forEach((s: string) => specificSlugs.add(s));
          } else {
            fetchAll = true;
          }
        } else if (c.type === 'biomarker' || c.type === 'trends' || c.type === 'range_gauge') {
          const slug = c.config.biomarker || (c.type === 'trends' ? selectedBiomarker : null);
          if (slug) specificSlugs.add(slug);
        }
      });
      
      const biomarkerCodes = fetchAll ? '' : Array.from(specificSlugs).join(',');
      
      if (!fetchAll && specificSlugs.size === 0) return;

      // Try cache first for the full trends data
      if (!biomarkerCodes) {
         const cached = await getCachedBiomarkerTrends(currentPatient.id, 'all-time');
         if (cached && cached.biomarkers) {
            setCardsData(cached.biomarkers);
         }
      }

      const data = await getBiomarkerTrends('', biomarkerCodes, 'all-time', currentPatient.id, '1 month');
      
      if (data && data.biomarkers) {
        setCardsData(data.biomarkers);
      }
    } catch (err) {
      console.error('Failed to load card data:', err);
    }
  }, [currentPatient?.id, cards, selectedBiomarker]);

  const loadAvailableBiomarkers = useCallback(async () => {
    try {
      if (currentPatient?.id) {
         const cached = await getCachedBiomarkerTrends(currentPatient.id, 'all-time');
         if (cached && cached.biomarkers) {
            const uniqueBiomarkers = Object.keys(cached.biomarkers).map(k => {
              const records = cached.biomarkers[k];
              return {
                id: records.length > 0 ? (records[0] as any).biomarker_id : null,
                slug: k,
                name: records.length > 0 && records[0].name ? records[0].name : k
              };
            });
            setAvailableBiomarkers(uniqueBiomarkers as any);
         }
      }

      const data = await getBiomarkerTrends('', '', 'all-time', currentPatient?.id, '1 month');
      if (data && data.biomarkers) {
        const uniqueBiomarkers = Object.keys(data.biomarkers).map(k => {
          const records = data.biomarkers[k];
          return {
            id: records.length > 0 ? (records[0] as any).biomarker_id : null,
            slug: k,
            name: records.length > 0 && records[0].name ? records[0].name : k
          };
        });
        
        const combined = [
          ...DEFAULT_BIOMARKERS.map(name => ({ id: null, slug: name.toLowerCase().replace(/\s+/g, '-'), name })),
          ...uniqueBiomarkers
        ];

        const unique = Array.from(new Map(combined.map(item => [item.slug, item])).values())
          .sort((a, b) => a.name.localeCompare(b.name));
          
        setAvailableBiomarkers(unique as any);
        
        if (!selectedBiomarker && unique.length > 0) {
           const defaultChoice = unique.find(b => b.slug.includes('cholesterol')) || unique[0];
           setSelectedBiomarker(defaultChoice.slug);
           
           setCards(prev => prev.map(c => 
             c.type === 'trends' && !c.config.biomarker 
               ? { ...c, config: { ...c.config, biomarker: defaultChoice.slug } } 
               : c
           ));
        }
      }
    } catch (err) {
      console.error(err);
    }
  }, [currentPatient?.id, selectedBiomarker, setSelectedBiomarker]);

  const loadDashboardData = useCallback(async () => {
    if (!currentPatient?.id) return;
    
    // Attempt cache load first for instant feedback
    try {
      const cached = await getCachedDashboardData(currentPatient.id, 'last-30-days');
      if (cached) {
         setDashboardData({
           summary: {
             totalDocuments: cached.summary.total_documents,
             totalObservations: cached.summary.total_observations,
             lastUpload: cached.summary.last_upload
           },
           recentDocuments: cached.recent_documents || [],
           alerts: cached.alerts,
           latestExamination: cached.latest_examination,
           latestImaging: cached.latest_imaging || [],
           latestLabs: cached.latest_labs || []
         });
      }
    } catch (e) {
      console.warn("Dashboard cache load failed", e);
    }

    try {
      const [data, meds, allergyData, exams, events] = await Promise.all([
        getDashboardData('', currentPatient.id, 'last-30-days'),
        getPatientMedications(currentPatient.id),
        getPatientAllergies(currentPatient.id),
        getExaminations(currentPatient.id),
        getPatientEvents(currentPatient.id)
      ]);
      
      setMedications(meds);
      setAllergies(allergyData);
      setExaminations(exams);
      setClinicalEvents(events);
      const dashboardData = data as any;
      setDashboardData({
        summary: {
          totalDocuments: dashboardData.summary.total_documents,
          totalObservations: dashboardData.summary.total_observations,
          lastUpload: dashboardData.summary.last_upload
        },
        recentDocuments: dashboardData.recent_documents || [],
        alerts: dashboardData.alerts,
        latestExamination: dashboardData.latest_examination,
        latestImaging: dashboardData.latest_imaging || [],
        latestLabs: dashboardData.latest_labs || []
      });
    } catch (error) {
      console.error(error);
    }
  }, [currentPatient?.id, setDashboardData]);

  const loadLayout = useCallback(async () => {
    if (!currentPatient?.id) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    try {
      const allLayouts = await getPatientLayouts(currentPatient.id);
      setLayoutsList(allLayouts);
      
      const active = allLayouts.find(l => l.is_default) || allLayouts[0];
      if (active) {
        setActiveLayout(active);
        setLayouts(active.layout_config);
        setCards(active.cards_config);
      } else {
        const newLayout = await createPatientLayout(currentPatient.id, {
          name: t('dashboard.default_layout_name'),
          is_default: true,
          layout_config: DEFAULT_LAYOUT_CONFIG,
          cards_config: DEFAULT_CARDS
        });
        setActiveLayout(newLayout);
        setLayouts(DEFAULT_LAYOUT_CONFIG);
        setCards(DEFAULT_CARDS);
        setLayoutsList([newLayout]);
      }
    } catch (err: any) {
      if (err?.response?.status === 404) {
        console.warn('Patient not found - using default layout');
        setLayouts(DEFAULT_LAYOUT_CONFIG);
        setCards(DEFAULT_CARDS);
      } else {
        console.error('Failed to load layout:', err);
        setLayouts(DEFAULT_LAYOUT_CONFIG);
        setCards(DEFAULT_CARDS);
      }
    } finally {
      setIsLoading(false);
    }
  }, [currentPatient?.id, setActiveLayout, setLayoutsList, t]);

  useEffect(() => {
    loadLayout();
    loadDashboardData();
    loadAvailableBiomarkers();
  }, [currentPatient?.id, loadLayout, loadDashboardData, loadAvailableBiomarkers]);

  useEffect(() => {
    loadAllCardData();
  }, [cards, currentPatient?.id, loadAllCardData]);

  const switchLayout = (layout: any) => {
    setActiveLayout(layout);
    setLayouts(layout.layout_config);
    setCards(layout.cards_config);
    setIsLayoutMenuOpen(false);
  };

  const createLayoutFromTemplate = async () => {
    if (!currentPatient?.id) return;
    try {
      const name = prompt(t('dashboard.layout_name_prompt'), `Template Layout ${layoutsList.length + 1}`);
      if (!name) return;
      
      const newLayout = await createPatientLayout(currentPatient.id, {
        name,
        is_default: false,
        layout_config: DEFAULT_LAYOUT_CONFIG,
        cards_config: DEFAULT_CARDS
      });
      
      const updatedList = await getPatientLayouts(currentPatient.id);
      setLayoutsList(updatedList);
      switchLayout(newLayout);
    } catch (err: any) {
      if (err?.response?.status === 404) {
        alert(`Patient not found. Please ensure the patient exists in the system.`);
      } else {
        console.error('Failed to create layout:', err);
      }
    }
  };

  const duplicateCurrentLayout = async () => {
    if (!currentPatient?.id) return;
    try {
      const name = prompt(t('dashboard.layout_name_prompt'), `${activeLayout?.name || 'Layout'} (Copy)`);
      if (!name) return;
      
      const newLayout = await createPatientLayout(currentPatient.id, {
        name,
        is_default: false,
        layout_config: layouts,
        cards_config: cards
      });
      
      const updatedList = await getPatientLayouts(currentPatient.id);
      setLayoutsList(updatedList);
      switchLayout(newLayout);
    } catch (err: any) {
      if (err?.response?.status === 404) {
        alert(`Patient not found. Please ensure the patient exists in the system.`);
      } else {
        console.error('Failed to create layout:', err);
      }
    }
  };

  const renameLayout = async () => {
    if (!currentPatient?.id || !activeLayout) return;
    try {
      const name = prompt('Enter new layout name:', activeLayout.name);
      if (!name || name === activeLayout.name) return;
      
      const updatedLayout = await updatePatientLayout(currentPatient.id, activeLayout.id, {
        name,
        layout_config: layouts,
        cards_config: cards
      });
      
      const updatedList = await getPatientLayouts(currentPatient.id);
      setLayoutsList(updatedList);
      setActiveLayout(updatedLayout);
      setIsLayoutMenuOpen(false);
    } catch (err: any) {
      console.error('Failed to rename layout:', err);
    }
  };

  const deleteCurrentLayout = async () => {
    if (!currentPatient?.id || !activeLayout || layoutsList.length <= 1) return;
    if (!confirm(t('dashboard.delete_layout_confirm'))) return;
    
    try {
      await deletePatientLayout(currentPatient.id, activeLayout.id);
      loadLayout();
    } catch (err: any) {
      if (err?.response?.status === 404) {
        alert('Layout not found.');
      } else {
        console.error('Failed to delete layout:', err);
      }
    }
  };

  const onLayoutChange = (_layout: any, allLayouts: any) => {
    setLayouts(allLayouts);
  };

  const saveCurrentLayout = async () => {
    if (!currentPatient?.id || !activeLayout) return;
    try {
      await updatePatientLayout(currentPatient.id, activeLayout.id, {
        layout_config: layouts,
        cards_config: cards
      });
      setIsEditMode(false);
    } catch (err: any) {
      if (err?.response?.status === 404) {
        alert('Patient or layout not found.');
      } else {
        console.error('Failed to save layout:', err);
      }
    }
  };

  const addCard = (type: string) => {
    const def = getCardDefinition(type);
    if (!def) return;

    const id = `card-${Date.now()}`;
    const firstBiomarker = availableBiomarkers[0];
    const defaultBiomarker = typeof firstBiomarker === 'object'
      ? (firstBiomarker as any).slug
      : (firstBiomarker || 'glucose');
    const biomarkerLabel = typeof firstBiomarker === 'object'
      ? (firstBiomarker as any).name
      : (firstBiomarker || 'Glucose');

    const config = resolveDefaultConfig(type, { defaultBiomarker, biomarkerLabel });
    const resolvedType = def.aliases?.includes(type) ? def.type : type;
    const newCard = { id, type: resolvedType, config };
    setCards([...cards, newCard]);

    const { w, h } = resolveDefaultLayout(type);
    const newLayout = { ...layouts };
    const colsFor: Record<string, number> = { lg: 12, md: 10, sm: 6, xs: 4, xxs: 2 };
    Object.keys(newLayout).forEach(breakpoint => {
      const cols = colsFor[breakpoint] ?? 6;
      newLayout[breakpoint] = [
        ...newLayout[breakpoint],
        { i: id, x: 0, y: Infinity, w: Math.min(w, cols), h }
      ];
    });
    setLayouts(newLayout);
    setIsAddCardMenuOpen(false);
  };

  const removeCard = (id: string) => {
    setCards(cards.filter(c => c.id !== id));
    const newLayout = { ...layouts };
    Object.keys(newLayout).forEach(breakpoint => {
      newLayout[breakpoint] = newLayout[breakpoint].filter((l: any) => l.i !== id);
    });
    setLayouts(newLayout);
  };

  const updateCardConfig = (id: string, newConfig: any) => {
    const oldCard = cards.find(c => c.id === id);
    if (oldCard && oldCard.config.biomarker !== newConfig.biomarker && oldCard.config.icon === newConfig.icon) {
      newConfig.icon = getBestIcon(newConfig.biomarker);
    }
    setCards(cards.map(c => c.id === id ? { ...c, config: newConfig } : c));
  };

  const resolveExtraProps = (type: string, card: any, cardData: any): Record<string, any> => {
    switch (type) {
      case 'biomarker':
      case 'range_gauge':
        return { availableBiomarkers };
      case 'trends':
        return {
          selectedBiomarker: card.config.biomarker || selectedBiomarker,
          setSelectedBiomarker: (val: string) => updateCardConfig(card.id, { ...card.config, biomarker: val }),
          trendsData: cardData,
          mockTrends: [],
          availableBiomarkers,
        };
      case 'multi_biomarker_comparison':
        return { trendsData: cardsData, availableBiomarkers };
      case 'health_summary':
        return { trendsData: cardsData, data: latestExamination };
      case 'labs':
        return { data: latestLabs, trendsData: cardsData, documents: recentDocuments, availableBiomarkers };
      case 'imaging':
        return { data: latestImaging };
      case 'latest_documents':
        return { data: recentDocuments };
      case 'examination':
        return { data: latestExamination, documents: recentDocuments };
      case 'health_calendar':
      case 'medication_calendar':
        return { medications, allergies, examinations, clinicalEvents };
      case 'allergy_alerts':
        return { data: allergies };
      default:
        return {};
    }
  };

  const renderCard = (card: any) => {
    const cardBiomarker = card.config.biomarker || (card.type === 'trends' ? selectedBiomarker : '');

    let cardData = undefined;
    if (cardBiomarker) {
      const keys = Object.keys(cardsData);
      const exactKey = keys.find(k => k === cardBiomarker);
      const lowerKey = keys.find(k => k.toLowerCase() === cardBiomarker.toLowerCase());
      const slugKey = keys.find(k => k === cardBiomarker.toLowerCase().replace(/\s+/g, '-'));
      const keyToUse = exactKey || lowerKey || slugKey;
      if (keyToUse) cardData = cardsData[keyToUse];
    }

    const Component = resolveCardComponent(card.type);
    if (!Component) return <div key={card.id}>Unknown Card Type</div>;

    const baseProps = {
      id: card.id,
      config: card.config,
      isEditMode,
      onRemove: removeCard,
      onUpdateConfig: updateCardConfig,
      data: cardData,
    };

    return <Component key={card.id} {...baseProps} {...resolveExtraProps(card.type, card, cardData)} />;
  };

  if (!currentPatient) {
    return <NoPatientState contextKey="dashboard" />;
  }

  if (isLoading) {
    return <LoadingState variant="section" showText={true} message={t('dashboard.assembling_metrics')} />;
  }

  return (
    <div className="w-full max-w-7xl mx-auto pb-20">
      <PageHeader
        title={t('dashboard.title')}
        subtitle={currentPatient ? `${currentPatient.name?.given?.join(' ')} ${currentPatient.name?.family}` : t('dashboard.all_patients')}
        icon={<img src={theme === 'dark' ? '/icon.svg' : '/icon-light.svg'} className="w-6 h-6" alt="Dashboard" />}
      />

      <StickyToolbar
        details={
          <div className="relative w-full sm:w-auto">
            <button 
              onClick={() => setIsLayoutMenuOpen(!isLayoutMenuOpen)}
              className="w-full sm:w-auto flex items-center justify-between sm:justify-start space-x-2 px-4 py-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl shadow-sm hover:bg-gray-50 dark:hover:bg-dark-bg transition-all active:scale-95"
            >
              <span className="text-sm font-bold text-gray-700 dark:text-dark-text truncate">{activeLayout?.name || t('dashboard.loading_layout')}</span>
              <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform ${isLayoutMenuOpen ? 'rotate-180' : ''}`} />
            </button>
            
            {isLayoutMenuOpen && (
              <div className="absolute left-0 mt-2 w-56 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl shadow-xl z-50 py-2">
                <div className="px-4 py-2 border-b border-gray-50 dark:border-dark-border">
                  <p className="text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-wider">{t('dashboard.your_layouts')}</p>
                </div>
                {layoutsList.map((l) => (
                  <button
                    key={l.id}
                    onClick={() => switchLayout(l)}
                    className={`w-full text-left px-4 py-2 text-sm transition-colors ${activeLayout?.id === l.id ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 font-semibold' : 'text-gray-700 dark:text-dark-text hover:bg-gray-50 dark:hover:bg-dark-border'}`}
                  >
                    {l.name} {l.is_default && t('dashboard.default_label')}
                  </button>
                ))}
                <div className="border-t border-gray-50 dark:border-dark-border mt-2 pt-2">
                  <button 
                    onClick={createLayoutFromTemplate}
                    className="w-full text-left px-4 py-2 text-sm text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors flex items-center space-x-2"
                  >
                    <LayoutTemplate className="w-4 h-4" />
                    <span>Create from Template</span>
                  </button>
                  <button 
                    onClick={duplicateCurrentLayout}
                    className="w-full text-left px-4 py-2 text-sm text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors flex items-center space-x-2"
                  >
                    <Copy className="w-4 h-4" />
                    <span>Duplicate Layout</span>
                  </button>
                  <button 
                    onClick={renameLayout}
                    className="w-full text-left px-4 py-2 text-sm text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors flex items-center space-x-2"
                  >
                    <Edit2 className="w-4 h-4" />
                    <span>Rename Layout</span>
                  </button>
                  {layoutsList.length > 1 && (
                    <button 
                      onClick={deleteCurrentLayout}
                      className="w-full text-left px-4 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors flex items-center space-x-2"
                    >
                      <Trash2 className="w-4 h-4" />
                      <span>{t('dashboard.delete_current')}</span>
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        }
        actions={
          <div className="flex items-center space-x-2 sm:space-x-3 w-full sm:w-auto overflow-x-auto sm:overflow-visible no-scrollbar pb-1 sm:pb-0">
            {isEditMode && (
              <>
                <div className="relative flex-shrink-0">
                  <button 
                    onClick={() => setIsAddCardMenuOpen(!isAddCardMenuOpen)}
                    className="flex items-center space-x-2 px-4 py-2.5 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded-xl font-bold text-sm hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-all active:scale-95 shadow-sm border border-blue-100 dark:border-blue-900/30"
                  >
                    <Plus className="w-4 h-4" />
                    <span className="hidden xs:inline">{t('dashboard.add_card')}</span>
                  </button>
                  {isAddCardMenuOpen && (
                    <div className="absolute right-0 mt-2 w-56 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl shadow-xl z-[60] py-2 animate-in fade-in slide-in-from-top-2 duration-200 max-h-[70vh] overflow-y-auto custom-scrollbar">
                      {ADDABLE_CARDS.map((def) => {
                        const Icon = def.icon;
                        return (
                          <button key={def.type} onClick={() => addCard(def.type)} className="w-full text-left px-4 py-2 text-sm hover:bg-gray-50 dark:hover:bg-dark-border dark:text-dark-text flex items-center space-x-2 transition-colors">
                            <Icon className={`w-4 h-4 flex-shrink-0 ${def.iconClassName || ''}`} />
                            <span>{t(def.labelKey)}</span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
                <button 
                  onClick={saveCurrentLayout}
                  className="flex-shrink-0 flex items-center space-x-2 px-6 py-2.5 bg-emerald-600 text-white rounded-xl font-bold text-sm hover:bg-emerald-700 transition-all shadow-lg shadow-emerald-200/50 dark:shadow-none active:scale-95"
                >
                  <Save className="w-4 h-4" />
                  <span className="hidden xs:inline">{t('dashboard.save_layout')}</span>
                </button>
              </>
            )}
            <button 
              onClick={() => setIsEditMode(!isEditMode)}
              className={`flex-shrink-0 p-2.5 rounded-xl transition-all shadow-sm border ${isEditMode ? 'bg-orange-600 text-white border-orange-700' : 'bg-white dark:bg-dark-surface hover:bg-gray-50 dark:hover:bg-dark-border text-gray-500 dark:text-dark-muted border-gray-200 dark:border-dark-border'}`}
              title={t('dashboard.edit_layout')}
            >
              <Settings className="w-5 h-5" />
            </button>
          </div>
        }
      />

      <ResponsiveGridLayout
        className="layout"
        layouts={layouts}
        breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480, xxs: 0 }}
        cols={{ lg: 12, md: 10, sm: 6, xs: 4, xxs: 2 }}
        rowHeight={80}
        onLayoutChange={onLayoutChange}
        isDraggable={isEditMode}
        isResizable={isEditMode}
        draggableCancel=".nodrag"
        useCSSTransforms={isEditMode}
        margin={{ lg: [24, 24], md: [20, 20], sm: [12, 12], xs: [8, 8], xxs: [4, 4] }}
      >
        {cards.map(renderCard)}
      </ResponsiveGridLayout>
    </div>
  );
}

export default Dashboard;
