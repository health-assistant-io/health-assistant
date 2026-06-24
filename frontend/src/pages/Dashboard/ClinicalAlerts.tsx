import { useEffect, useState } from 'react';
import { ShieldAlert, AlertTriangle, User, ExternalLink, Search, ChevronRight } from 'lucide-react';
import { getActiveAllergies, AllergyIntolerance } from '../../services/allergyService';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { NoPatientState } from '../../components/ui/NoPatientState';

interface AllergyWithPatient extends AllergyIntolerance {
  patient_name_display: string;
}

function ClinicalAlerts() {
  const { t } = useTranslation();
  const { currentPatient } = usePatientStore();
  const [allergies, setAllergies] = useState<AllergyWithPatient[]>([]);
  const [loading, setLoading] = useState(true);
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  const [criticalityFilter, setCriticalityFilter] = useState('all');
  const navigate = useNavigate();

  useEffect(() => {
    loadAllergies();
  }, []);

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  const loadAllergies = async () => {
    try {
      setLoading(true);
      const data = await getActiveAllergies();
      setAllergies(data as any);
    } catch (error) {
      console.error('Failed to load active allergies:', error);
    } finally {
      setLoading(false);
    }
  };

  const filteredAllergies = allergies.filter(allergy => {
    const matchesSearch = 
      allergy.code.text.toLowerCase().includes(searchTerm.toLowerCase()) ||
      allergy.patient_name_display.toLowerCase().includes(searchTerm.toLowerCase());
    
    const matchesCriticality = 
      criticalityFilter === 'all' || 
      allergy.criticality === criticalityFilter;

    return matchesSearch && matchesCriticality;
  });

  const getCriticalityStyles = (criticality?: string) => {
    switch (criticality) {
      case 'high':
        return 'bg-red-50 dark:bg-red-900/10 border-red-100 dark:border-red-900/30 text-red-700 dark:text-red-400';
      case 'low':
        return 'bg-blue-50 dark:bg-blue-900/10 border-blue-100 dark:border-blue-900/30 text-blue-700 dark:text-blue-400';
      default:
        return 'bg-gray-50 dark:bg-dark-bg/50 border-gray-100 dark:border-dark-border text-gray-700 dark:text-dark-text';
    }
  };

  if (!currentPatient) {
    return <NoPatientState icon={ShieldAlert} contextKey="alerts" />;
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <ShieldAlert className="w-8 h-8 text-blue-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-10">
      <PageHeader
        title={t('alerts_page.title')}
        subtitle={t('alerts_page.subtitle')}
        icon={<ShieldAlert className="w-8 h-8 text-red-500" />}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex items-center space-x-3">
            <select 
              className="px-4 py-2 border border-gray-200 dark:border-dark-border rounded-xl text-sm outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-dark-surface dark:text-dark-text shadow-sm"
              value={criticalityFilter}
              onChange={(e) => setCriticalityFilter(e.target.value)}
            >
              <option value="all">{t('alerts_page.filters.all')}</option>
              <option value="high">{t('alerts_page.filters.high')}</option>
              <option value="low">{t('alerts_page.filters.low')}</option>
            </select>
          </div>
        }
      />

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filteredAllergies.length > 0 ? filteredAllergies.map((allergy) => (
          <div 
            key={allergy.id}
            className={`rounded-3xl border p-6 shadow-sm hover:shadow-md transition-all group ${getCriticalityStyles(allergy.criticality)}`}
          >
            <div className="flex justify-between items-start mb-4">
                <div className={`p-3 rounded-2xl ${allergy.criticality === 'high' ? 'bg-red-100/50 dark:bg-red-900/40' : 'bg-blue-100/50 dark:bg-blue-900/40'}`}>
                    <AlertTriangle className={`w-6 h-6 ${allergy.criticality === 'high' ? 'text-red-600 dark:text-red-400' : 'text-blue-600 dark:text-blue-400'}`} />
                </div>
                <button 
                    onClick={() => navigate(`/patients/${allergy.patient_id}`)}
                    className="p-2 hover:bg-white dark:hover:bg-dark-surface rounded-full transition-colors opacity-0 group-hover:opacity-100"
                    title={t('alerts_page.view_profile')}
                >
                    <ExternalLink className="w-4 h-4 dark:text-dark-text" />
                </button>
            </div>

            <h3 className="text-xl font-bold mb-1 dark:text-dark-text">{allergy.code.text}</h3>
            <div className="flex items-center space-x-2 text-sm font-medium opacity-70 mb-4 dark:text-dark-muted">
                <User className="w-3.5 h-3.5" />
                <span>{allergy.patient_name_display}</span>
            </div>

            <div className="space-y-3">
                {allergy.reactions && allergy.reactions.length > 0 && (
                    <div className="bg-white/40 dark:bg-black/20 p-3 rounded-xl border border-black/5 dark:border-white/5">
                        <p className="text-[10px] font-black uppercase tracking-widest opacity-40 mb-1 dark:text-dark-muted">{t('alerts_page.observed_reactions')}</p>
                        <p className="text-xs font-semibold dark:text-dark-text">{allergy.reactions.map((r: any) => r.manifestation).join(', ')}</p>
                    </div>
                )}
                
                {allergy.note && (
                    <div className="bg-white/40 dark:bg-black/20 p-3 rounded-xl border border-black/5 dark:border-white/5">
                        <p className="text-[10px] font-black uppercase tracking-widest opacity-40 mb-1 dark:text-dark-muted">{t('alerts_page.clinical_note')}</p>
                        <p className="text-xs italic leading-relaxed line-clamp-2 dark:text-dark-text">"{allergy.note}"</p>
                    </div>
                )}
            </div>

            <div className="mt-6 pt-4 border-t border-black/5 dark:border-white/5 flex justify-between items-center">
                <span className="text-[10px] font-bold uppercase tracking-tighter opacity-50 dark:text-dark-muted">
                    {t('alerts_page.category_label')}: {allergy.category || 'N/A'}
                </span>
                <button 
                    onClick={() => navigate(`/patients/${allergy.patient_id}`)}
                    className="flex items-center space-x-1 text-xs font-bold hover:underline dark:text-dark-text"
                >
                    <span>{t('alerts_page.open_chart')}</span>
                    <ChevronRight className="w-3 h-3" />
                </button>
            </div>
          </div>
        )) : (
          <div className="col-span-full bg-white dark:bg-dark-surface rounded-3xl border border-dashed border-gray-200 dark:border-dark-border py-20 flex flex-col items-center justify-center text-center">
            <div className="w-20 h-20 bg-green-50 dark:bg-green-900/20 rounded-full flex items-center justify-center mb-4">
                <ShieldAlert className="w-10 h-10 text-green-500 dark:text-green-400 opacity-20" />
            </div>
            <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text">{t('alerts_page.no_alerts')}</h3>
            <p className="text-gray-500 dark:text-dark-muted max-w-xs mt-2">{t('alerts_page.no_alerts_subtitle')}</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default ClinicalAlerts;
