import React from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { 
  X, 
  FlaskConical, 
  Filter, 
  Info
} from 'lucide-react';
import { useBiomarkers } from '../../../hooks/useBiomarkers';
import { useBiomarkerPrecisionProfile } from '../../../hooks/useBiomarkerPrecision';
import { BiomarkerObservation } from '../../../types/biomarker';
import { DOCUMENT_CATEGORIES } from '../../../constants/categories';
import { getFinalStatus, isAbnormal, formatUnit, formatBiomarkerValue } from '../../../utils/biomarkerUtils';
import { filterBiomarkers } from '../../../utils/searchUtils';
import { BiomarkerInfoModal } from '../shared/BiomarkerInfoModal';
import { BiomarkerStatusIndicator } from '../shared/BiomarkerStatusIndicator';
import { SearchableBiomarkerSelect } from '../shared/SearchableBiomarkerSelect';


export const BiomarkersCard = React.forwardRef((props: any, ref: any) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const precisionProfile = useBiomarkerPrecisionProfile();
  const { id, isEditMode, onRemove, style, className, onMouseDown, onMouseUp, onTouchEnd, children, data, documents, trendsData, config, onUpdateConfig } = props;
  const [selectedInfo, setSelectedInfo] = React.useState<any>(null);
  const [showConfig, setShowConfig] = React.useState(false);
  
  const selectedCategories = config?.categories || [];
  const selectedSpecificBiomarkers = config?.biomarkers || [];
  const statusFilter = config?.statusFilter || [];
  const sortBy = config?.sortBy || 'date'; // 'date', 'alerts', 'lowest', 'highest'
  const limit = config?.limit || 15;

  // Use the new hook with both documents and trendsData to ensure we have data
  const { biomarkers } = useBiomarkers({ 
    documents: documents || [],
    trendsData: trendsData
});

  // Get all unique biomarkers from the patient data for selection
  const allAvailableBiomarkers = React.useMemo(() => {
    const map = new Map<string, BiomarkerObservation>();
    biomarkers.forEach(b => {
      const slug = b.slug || b.displayName.toLowerCase().replace(/\s+/g, '-');
      if (!map.has(slug)) {
        map.set(slug, b);
      }
    });
    return Array.from(map.values()).sort((a, b) => a.displayName.localeCompare(b.displayName));
  }, [biomarkers]);

  // Filter and process biomarkers based on config
  const processedBiomarkers = React.useMemo(() => {
    let filtered = biomarkers;

    // Filter by categories if specified
    if (selectedCategories.length > 0) {
      filtered = filtered.filter(b => {
        const techCat = b._rawJson?.techCategory || b._rawJson?.document_category || 'other';
        const clinicalGroups = b._rawJson?.clinicalGroups || [];
        
        const matchesTech = selectedCategories.includes(techCat);
        const matchesClinical = clinicalGroups.some((g: string) => selectedCategories.includes(g));
        
        const isGeneralLab = techCat === 'laboratory-tests' || techCat === 'other';
        const selectedLab = selectedCategories.includes('laboratory-tests') || selectedCategories.includes('other');
        
        return matchesTech || matchesClinical || (isGeneralLab && selectedLab);
      });
    }

    // Filter by specific biomarkers if specified
    if (selectedSpecificBiomarkers.length > 0) {
      filtered = filtered.filter(b => {
        const slug = b.slug || b.displayName.toLowerCase().replace(/\s+/g, '-');
        return selectedSpecificBiomarkers.includes(slug);
      });
    }

    // Filter by status if specified
    if (statusFilter.length > 0) {
      filtered = filtered.filter(b => {
        const status = getFinalStatus(b).toLowerCase();
        return statusFilter.some((f: string) => status.includes(f));
      });
    }

    // Group by slug to get the latest for each unique biomarker
    const latestMap = new Map<string, BiomarkerObservation>();
    filtered.forEach(b => {
      const slug = b.slug || b.displayName.toLowerCase().replace(/\s+/g, '-');
      const existing = latestMap.get(slug);
      if (!existing || new Date(b.source.date).getTime() > new Date(existing.source.date).getTime()) {
        latestMap.set(slug, b);
      }
    });

    let result = Array.from(latestMap.values());

    // Sort logic
    if (sortBy === 'alerts') {
      result.sort((a, b) => {
        const aStatus = getFinalStatus(a);
        const bStatus = getFinalStatus(b);
        const aIsAbnormal = isAbnormal(aStatus);
        const bIsAbnormal = isAbnormal(bStatus);
        
        if (aIsAbnormal && !bIsAbnormal) return -1;
        if (!aIsAbnormal && bIsAbnormal) return 1;
        return new Date(b.source.date).getTime() - new Date(a.source.date).getTime();
      });
    } else if (sortBy === 'lowest') {
      result.sort((a, b) => {
        const aStatus = getFinalStatus(a).toLowerCase();
        const bStatus = getFinalStatus(b).toLowerCase();
        
        const order: Record<string, number> = { 'low': 1, 'normal': 2, 'high': 3 };
        const aOrder = order[aStatus] || 2;
        const bOrder = order[bStatus] || 2;
        
        if (aOrder !== bOrder) return aOrder - bOrder;
        return new Date(b.source.date).getTime() - new Date(a.source.date).getTime();
      });
    } else if (sortBy === 'highest') {
      result.sort((a, b) => {
        const aStatus = getFinalStatus(a).toLowerCase();
        const bStatus = getFinalStatus(b).toLowerCase();
        
        const order: Record<string, number> = { 'high': 1, 'low': 2, 'normal': 3 };
        const aOrder = order[aStatus] || 3;
        const bOrder = order[bStatus] || 3;
        
        if (aOrder !== bOrder) return aOrder - bOrder;
        return new Date(b.source.date).getTime() - new Date(a.source.date).getTime();
      });
    } else {
      result.sort((a, b) => new Date(b.source.date).getTime() - new Date(a.source.date).getTime());
    }

    return result.slice(0, limit);
  }, [biomarkers, selectedCategories, selectedSpecificBiomarkers, statusFilter, sortBy, limit]);
  
  const labs = processedBiomarkers.map(b => {
    const status = getFinalStatus(b);
    
    return {
      name: b.displayName,
      result: formatBiomarkerValue(b.value.raw, precisionProfile),
      unit: b.unit.rawSymbol,
      status: status,
      date: b.source.date,
      info: b.info,
      biomarker_id: b.definitionId,
      slug: b.slug || b.displayName.toLowerCase().replace(/\s+/g, '-')
    };
  });

  const toggleCategory = (catId: string) => {
    const newCategories = selectedCategories.includes(catId)
      ? selectedCategories.filter((c: string) => c !== catId)
      : [...selectedCategories, catId];
    onUpdateConfig(id, { ...config, categories: newCategories });
  };

  const toggleBiomarker = (slug: string) => {
    const newBiomarkers = selectedSpecificBiomarkers.includes(slug)
      ? selectedSpecificBiomarkers.filter((s: string) => s !== slug)
      : [...selectedSpecificBiomarkers, slug];
    onUpdateConfig(id, { ...config, biomarkers: newBiomarkers });
  };

  const toggleStatusFilter = (status: string) => {
    const newFilters = statusFilter.includes(status)
      ? statusFilter.filter((s: string) => s !== status)
      : [...statusFilter, status];
    onUpdateConfig(id, { ...config, statusFilter: newFilters });
  };

  const renderLabRow = (lab: any, idx: number) => {
    const targetId = lab.biomarker_id;
    return (
      <tr key={idx} className="border-b border-gray-50 dark:border-dark-border hover:bg-gray-50/50 dark:hover:bg-dark-bg/50 transition-colors group/row">
        <td className="py-3 px-4 font-bold text-gray-900 dark:text-dark-text flex items-center">
          {targetId ? (
            <Link to={`/biomarkers/details/${targetId}`} className="hover:text-blue-600 transition-colors">
              {lab.name}
            </Link>
          ) : (
            <span>{lab.name}</span>
          )}

          {lab.info && (
            <button 
              type="button"
              onClick={(e) => { 
                e.preventDefault();
                e.stopPropagation(); 
                setSelectedInfo({ info: lab.info, name: lab.name }); 
              }}
              className="ml-2 p-1 text-blue-400 transition-colors hover:text-blue-600 relative z-30"
              title={t('common.details')}
            >
              <Info className="w-3.5 h-3.5" />
            </button>
          )}
        </td>
        <td className="py-3 px-4 font-bold text-gray-900 dark:text-dark-text">{lab.result} <span className="text-[10px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-tighter">{formatUnit(lab.unit)}</span></td>
        <td className="py-3 px-4">
          <BiomarkerStatusIndicator interpretation={lab.status} compact={true} className="!items-start" />
        </td>
        <td className="py-3 px-4 text-right text-gray-400 dark:text-dark-muted font-bold text-[11px] whitespace-nowrap">{new Date(lab.date).toLocaleDateString()}</td>
      </tr>
    );
  };

  return (
    <div 
      ref={ref}
      style={style}
      className={`${className || ''} bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-6 flex flex-col relative group ${isEditMode ? '' : 'overflow-hidden'}`}
      onMouseDown={onMouseDown}
      onMouseUp={onMouseUp}
      onTouchEnd={onTouchEnd}
    >
      {isEditMode && onRemove && (
        <button 
          onClick={(e) => { e.stopPropagation(); onRemove(id); }}
          className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full p-1 shadow-lg opacity-0 group-hover:opacity-100 transition-opacity z-[60] hover:bg-red-600 active:scale-95"
        >
          <X className="w-3 h-3" />
        </button>
      )}
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center space-x-2">
          <FlaskConical className="w-5 h-5 text-blue-500" />
          <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">
            {t('common.biomarkers')}
          </h3>
        </div>
        
        <div className="flex items-center space-x-2">
          {isEditMode && (
            <button 
              onClick={(e) => { e.stopPropagation(); setShowConfig(!showConfig); }}
              className={`p-1.5 rounded-lg transition-colors ${showConfig ? 'bg-blue-100 text-blue-600' : 'hover:bg-gray-100 dark:hover:bg-dark-border text-gray-400'}`}
              title={t('common.filters')}
            >
              <Filter className="w-4 h-4" />
            </button>
          )}
          <button 
            onClick={() => navigate('/biomarkers')}
            className="text-sm font-bold text-blue-600 dark:text-blue-400 hover:underline"
          >
            {t('common.view_all')}
          </button>
        </div>
      </div>

      {isEditMode && showConfig && (
        <div className="mb-6 p-4 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-100 dark:border-dark-border space-y-4 animate-in slide-in-from-top-2 duration-200 nodrag relative" onMouseDown={e => e.stopPropagation()}>
          <button 
            onClick={(e) => { e.stopPropagation(); setShowConfig(false); }}
            className="absolute top-2 right-2 p-1 text-gray-400 hover:text-gray-600 dark:hover:text-dark-text transition-colors"
            title={t('common.dismiss')}
          >
            <X className="w-4 h-4" />
          </button>
          <div className="space-y-2">
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">{t('dashboard.config.layout_display')}</p>
            <div className="flex flex-wrap gap-2">
              {DOCUMENT_CATEGORIES.map(cat => (
                <button
                  key={cat.id}
                  onClick={() => toggleCategory(cat.id)}
                  className={`px-2 py-1 text-[10px] font-bold rounded-lg border transition-all ${
                    selectedCategories.includes(cat.id)
                      ? 'bg-blue-600 text-white border-blue-700'
                      : 'bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-text border-gray-200 dark:border-dark-border hover:bg-gray-50'
                  }`}
                >
                  {cat.label}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">{t('dashboard.config.select_biomarker')}</p>
            <SearchableBiomarkerSelect
              multiple={true}
              options={allAvailableBiomarkers}
              value={selectedSpecificBiomarkers}
              onChange={(newBiomarkers) => onUpdateConfig(id, { ...config, biomarkers: newBiomarkers })}
              placeholder={t('dashboard.config.select_biomarker')}
            />
          </div>

          <div className="flex items-center justify-between pt-2 border-t border-gray-200 dark:border-dark-border">
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">Max Results</p>
            <div className="flex bg-white dark:bg-dark-surface p-0.5 rounded-lg border border-gray-200 dark:border-dark-border">
              {[5, 10, 15, 25, 50].map(val => (
                <button 
                  key={val}
                  onClick={() => onUpdateConfig(id, { ...config, limit: val })}
                  className={`px-2 py-1 text-[10px] font-bold rounded-md transition-all ${limit === val ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-400 hover:text-gray-600'}`}
                >
                  {val}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2 pt-2 border-t border-gray-200 dark:border-dark-border">
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">Status Filter</p>
            <div className="flex flex-wrap gap-2">
              {[
                { id: 'low', label: 'Lowest', color: 'orange' },
                { id: 'normal', label: 'Normal', color: 'blue' },
                { id: 'high', label: 'High', color: 'red' }
              ].map(status => (
                <button
                  key={status.id}
                  onClick={() => toggleStatusFilter(status.id)}
                  className={`px-3 py-1 text-[10px] font-bold rounded-lg border transition-all ${
                    statusFilter.includes(status.id)
                      ? `bg-${status.color}-600 text-white border-${status.color}-700 shadow-md`
                      : 'bg-white dark:bg-dark-surface text-gray-600 dark:text-dark-text border-gray-200 dark:border-dark-border hover:bg-gray-50'
                  }`}
                >
                  {status.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-col space-y-2 pt-2 border-t border-gray-200 dark:border-dark-border">
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">Status Criticality Sort By</p>
            <div className="flex flex-wrap gap-1 bg-white dark:bg-dark-surface p-0.5 rounded-lg border border-gray-200 dark:border-dark-border">
              <button 
                onClick={() => onUpdateConfig(id, { ...config, sortBy: 'date' })}
                className={`flex-1 px-2 py-1 text-[10px] font-bold rounded-md transition-all ${sortBy === 'date' ? 'bg-blue-50 text-blue-600 shadow-sm border border-blue-100' : 'text-gray-400 hover:text-gray-600 border border-transparent'}`}
              >
                Date
              </button>
              <button 
                onClick={() => onUpdateConfig(id, { ...config, sortBy: 'alerts' })}
                className={`flex-1 px-2 py-1 text-[10px] font-bold rounded-md transition-all ${sortBy === 'alerts' ? 'bg-red-50 text-red-600 shadow-sm border border-red-100' : 'text-gray-400 hover:text-red-600 border border-transparent'}`}
              >
                Alerts
              </button>
              <button 
                onClick={() => onUpdateConfig(id, { ...config, sortBy: 'lowest' })}
                className={`flex-1 px-2 py-1 text-[10px] font-bold rounded-md transition-all ${sortBy === 'lowest' ? 'bg-orange-50 text-orange-600 shadow-sm border border-orange-100' : 'text-gray-400 hover:text-orange-600 border border-transparent'}`}
              >
                Lowest
              </button>
              <button 
                onClick={() => onUpdateConfig(id, { ...config, sortBy: 'highest' })}
                className={`flex-1 px-2 py-1 text-[10px] font-bold rounded-md transition-all ${sortBy === 'highest' ? 'bg-red-50 text-red-600 shadow-sm border border-red-100' : 'text-gray-400 hover:text-red-600 border border-transparent'}`}
              >
                Highest
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="overflow-x-auto overflow-y-auto flex-1 custom-scrollbar">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-gray-100 dark:border-dark-border text-xs text-gray-400 dark:text-dark-muted font-bold uppercase tracking-wider">
              <th className="pb-3 px-4">{t('examinations.key_biomarkers')}</th>
              <th className="pb-3 px-4">Result</th>
              <th className="pb-3 px-4">Status</th>
              <th className="pb-3 px-4 text-right">Date</th>
            </tr>
          </thead>
          <tbody className="text-sm">
            {labs.length > 0 ? labs.map(renderLabRow) : (
              <tr>
                <td colSpan={4} className="py-10 text-center text-gray-400 dark:text-dark-muted">{t('dashboard.status.no_biomarkers')}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {selectedInfo && (
        <BiomarkerInfoModal 
          info={selectedInfo.info} 
          name={selectedInfo.name} 
          onClose={() => setSelectedInfo(null)} 
        />
      )}
      {children}
    </div>
  );
});
