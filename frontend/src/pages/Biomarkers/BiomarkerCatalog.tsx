import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Search, Plus, ListTree, Info, Edit3, X, Save, Trash2, CheckCircle, ChevronDown, CheckSquare, Square, RefreshCw } from 'lucide-react';
import { LoadingState } from '../../components/ui/LoadingState';
import { RichTextEditor } from '../../components/ui/RichTextEditor';
import { CreateBiomarkerModal } from '../../components/examinations/CreateBiomarkerModal';
import { UnitSelector } from '../../components/ui/UnitSelector';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import biomarkerService from '../../services/biomarkerService';
import { Biomarker, Unit } from '../../types/biomarker';
import { useUIStore } from '../../store/slices/uiSlice';
import { formatUnit } from '../../utils/biomarkerUtils';
import { filterBiomarkers } from '../../utils/searchUtils';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';

const BiomarkerCatalog: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [biomarkers, setBiomarkers] = useState<Biomarker[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [loading, setLoading] = useState(true);
  const searchTerm = useUIStore(state => state.pageSearchTerm);
  const setSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const setIsPageSearchSupported = useUIStore(state => state.setIsPageSearchSupported);
  const [editingBiomarker, setEditingBiomarker] = useState<Biomarker | null>(null);
  const [infoText, setInfoText] = useState('');
  const [rangeMin, setRangeMin] = useState<string>('');
  const [rangeMax, setRangeMax] = useState<string>('');
  const [preferredUnitId, setPreferredUnitId] = useState<string>('');
  const [isSaving, setIsSaving] = useState(false);
  const [viewingInfo, setViewingInfo] = useState<Biomarker | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isEditMode, setIsEditMode] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const showConfirmation = useUIStore(state => state.showConfirmation);

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    setIsPageSearchSupported(true);
    return () => {
      setIsPageSearchSupported(false);
      setSearchTerm('');
    };
  }, [setIsPageSearchSupported, setSearchTerm]);

  const loadData = async () => {
    try {
      const [bioData, unitsData] = await Promise.all([
        biomarkerService.getAllBiomarkers(),
        biomarkerService.getUnits()
      ]);
      setBiomarkers(bioData);
      setUnits(unitsData);
    } catch (error) {
      console.error("Failed to load data", error);
    } finally {
      setLoading(false);
    }
  };

  const handleEditInfo = (biomarker: Biomarker) => {
    setEditingBiomarker(biomarker);
    setInfoText(biomarker.info || '');
    setRangeMin(biomarker.reference_range_min?.toString() || '');
    setRangeMax(biomarker.reference_range_max?.toString() || '');
    setPreferredUnitId(biomarker.preferred_unit_id || '');
  };

  const handleSaveInfo = async () => {
    if (!editingBiomarker) return;
    setIsSaving(true);
    try {
      await biomarkerService.updateBiomarker(editingBiomarker.id, { 
        info: infoText,
        reference_range_min: rangeMin === '' ? null : parseFloat(rangeMin),
        reference_range_max: rangeMax === '' ? null : parseFloat(rangeMax),
        preferred_unit_id: preferredUnitId === '' ? null : preferredUnitId
      });
      
      // Reload biomarkers
      const data = await biomarkerService.getAllBiomarkers();
      setBiomarkers(data);
      setEditingBiomarker(null);
    } catch (error) {
      console.error("Failed to update biomarker info", error);
      alert("Failed to save info");
    } finally {
      setIsSaving(false);
    }
  };

  const toggleSelectAll = () => {
    if (selectedIds.length === filteredBiomarkers.length) {
      setSelectedIds([]);
    } else {
      setSelectedIds(filteredBiomarkers.map(b => b.id));
    }
  };

  const toggleSelectOne = (id: string) => {
    if (selectedIds.includes(id)) {
      setSelectedIds(selectedIds.filter(i => i !== id));
    } else {
      setSelectedIds([...selectedIds, id]);
    }
  };

  const handleBulkDelete = () => {
    if (selectedIds.length === 0) return;
    
    showConfirmation({
      title: 'Delete Biomarkers',
      message: `Are you sure you want to delete ${selectedIds.length} biomarkers? This action cannot be undone and will unbind related patient observations.`,
      confirmLabel: 'Delete Selection',
      confirmVariant: 'danger',
      onConfirm: async () => {
        setIsDeleting(true);
        // Optimistic update
        const remainingIds = selectedIds;
        setBiomarkers(prev => prev.filter(b => !remainingIds.includes(b.id)));
        
        try {
          await biomarkerService.bulkDeleteBiomarkers(selectedIds);
          // Trust the optimistic update, no need to loadData() immediately 
          // unless we want to sync other metadata.
          setSelectedIds([]);
          setIsEditMode(false);
        } catch (error) {
          console.error("Failed to delete biomarkers", error);
          await loadData(); // Rollback on error to restore the list
        } finally {
          setIsDeleting(false);
        }
      }
    });
  };

  const handleSingleDelete = (biomarker: Biomarker) => {
    showConfirmation({
      title: 'Delete Biomarker',
      message: `Are you sure you want to delete "${biomarker.name}"? This will not delete patient results but will remove this template from the catalog.`,
      confirmLabel: 'Delete',
      confirmVariant: 'danger',
      onConfirm: async () => {
        // Optimistic update
        const deletedId = biomarker.id;
        setBiomarkers(prev => prev.filter(b => b.id !== deletedId));
        
        try {
          await biomarkerService.deleteBiomarker(biomarker.id);
          // Trust the optimistic update, no need to reload the whole list
        } catch (e) {
          console.error("Failed to delete", e);
          await loadData(); // Rollback on error to restore the item
        }
      }
    });
  };

  const filteredBiomarkers = filterBiomarkers(biomarkers, searchTerm);

  const handleCreateSuccess = (newBiomarker: Biomarker) => {
    loadData();
    navigate(`/biomarkers/details/${newBiomarker.id}`);
  };

  if (loading) {
    return <LoadingState variant="section" showText={false} />;
  }


  return (
    <div className="max-w-7xl mx-auto pb-20">
      <PageHeader
        title={t('biomarker_catalog.title')}
        subtitle={t('biomarker_catalog.subtitle')}
        icon={<ListTree className="w-8 h-8" />}
        breadcrumbs={[
          { label: t('biomarkers.title'), path: '/biomarkers' }
        ]}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <div className="flex flex-wrap items-center gap-3">
            {isEditMode && selectedIds.length > 0 && (
              <button 
                onClick={handleBulkDelete}
                disabled={isDeleting}
                className="flex items-center space-x-2 px-6 py-2.5 bg-red-50 dark:bg-red-900/10 text-red-600 dark:text-red-400 border border-red-100 dark:border-red-900/20 rounded-xl font-bold text-sm hover:bg-red-100 dark:hover:bg-red-900/20 transition-all active:scale-95 disabled:opacity-50"
              >
                <Trash2 className="w-4 h-4" />
                <span>{t('biomarker_catalog.delete_selection')} ({selectedIds.length})</span>
              </button>
            )}

            <button
              onClick={() => {
                setIsEditMode(!isEditMode);
                setSelectedIds([]);
              }}
              className={`flex items-center space-x-2 px-4 py-2.5 rounded-xl font-bold text-sm transition-all shadow-sm active:scale-95 border ${
                isEditMode 
                  ? 'bg-blue-600 text-white border-blue-700 shadow-lg shadow-blue-200/50' 
                  : 'bg-white dark:bg-dark-surface text-gray-700 dark:text-dark-text border-gray-200 dark:border-dark-border hover:bg-gray-50 dark:hover:bg-dark-bg'
              }`}
            >
              {isEditMode ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />}
              <span>{isEditMode ? t('biomarker_catalog.finish_editing') : t('biomarker_catalog.edit_catalog')}</span>
            </button>

            <button 
              onClick={loadData}
              disabled={loading}
              className="p-2.5 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-xl text-gray-400 hover:text-blue-600 transition-all shadow-sm active:scale-95 disabled:opacity-50"
              title={t('biomarker_catalog.refresh_catalog')}
            >
              <RefreshCw className={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button 
              onClick={() => setIsCreateModalOpen(true)}
              className="flex items-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95"
            >
              <Plus className="w-4 h-4" />
              <span>{t('biomarker_catalog.add_metric')}</span>
            </button>
          </div>
        }
      />

      <CreateBiomarkerModal 
        isOpen={isCreateModalOpen} 
        onClose={() => setIsCreateModalOpen(false)} 
        onSuccess={handleCreateSuccess} 
      />

      <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden mb-8">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
            <thead className="bg-gray-50 dark:bg-dark-bg sticky top-0 z-10 shadow-sm">
              <tr>
                {isEditMode && (
                  <th className="px-6 py-4 text-left bg-gray-50 dark:bg-dark-bg w-10">
                    <div className="flex items-center">
                      <input 
                        type="checkbox" 
                        className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 transition-all cursor-pointer"
                        checked={selectedIds.length === filteredBiomarkers.length && filteredBiomarkers.length > 0}
                        onChange={toggleSelectAll}
                      />
                    </div>
                  </th>
                )}
                  <th className="px-6 py-4 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest bg-gray-50 dark:bg-dark-bg">{t('biomarker_catalog.table.name')}</th>
                <th className="px-6 py-4 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest bg-gray-50 dark:bg-dark-bg">CODE / ID</th>
                <th className="px-6 py-4 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest bg-gray-50 dark:bg-dark-bg">{t('biomarker_catalog.table.range')}</th>
                <th className="px-6 py-4 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest bg-gray-50 dark:bg-dark-bg">{t('biomarker_catalog.table.aliases')}</th>
                <th className="px-6 py-4 text-left text-[10px] font-bold text-gray-400 dark:text-dark-muted uppercase tracking-widest bg-gray-50 dark:bg-dark-bg w-20">{t('biomarker_catalog.table.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
              {filteredBiomarkers.map((b) => (
                <tr key={b.id} className={`hover:bg-gray-50/50 dark:hover:bg-dark-bg/50 transition-colors group ${selectedIds.includes(b.id) ? 'bg-blue-50/30 dark:bg-blue-900/10' : ''}`}>
                  {isEditMode && (
                    <td className="px-6 py-4 whitespace-nowrap">
                      <input 
                        type="checkbox" 
                        className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 transition-all cursor-pointer"
                        checked={selectedIds.includes(b.id)}
                        onChange={() => toggleSelectOne(b.id)}
                      />
                    </td>
                  )}
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-bold text-gray-900 dark:text-dark-text flex items-center">
                    <Link to={`/biomarkers/details/${b.id}`} className="hover:text-blue-600 transition-colors">
                      {b.name}
                    </Link>
                    <button 
                      onClick={() => setViewingInfo(b)}
                      className="ml-2 p-1 text-blue-400 opacity-0 group-hover:opacity-100 transition-opacity hover:text-blue-600"
                      title={t('biomarker_catalog.view_info')}
                    >
                      <Info className="w-3.5 h-3.5" />
                    </button>
                  </td>

                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-blue-600 dark:text-blue-400">
                    <div className="flex flex-col space-y-1">
                       <span className="text-[9px] uppercase font-black text-gray-400 tracking-widest">{b.coding_system === 'loinc' ? 'LOINC' : 'CUSTOM'}</span>
                       <span className="bg-blue-50 dark:bg-blue-900/20 px-2 py-0.5 rounded text-[11px] font-mono w-fit">{b.code || b.slug}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-500 dark:text-dark-muted">
                    {b.reference_range_min != null || b.reference_range_max != null ? (
                      <div className="flex flex-col">
                        <span className="px-2 py-1 bg-gray-100 dark:bg-dark-bg rounded text-[10px] font-bold font-mono w-fit">
                          {b.reference_range_min != null && b.reference_range_max != null 
                            ? `${b.reference_range_min} - ${b.reference_range_max}`
                            : b.reference_range_min != null 
                              ? `> ${b.reference_range_min}`
                              : `< ${b.reference_range_max}`
                          }
                        </span>
                        {b.preferred_unit_symbol && (
                          <span className="text-[9px] text-gray-400 mt-1 ml-1 uppercase font-bold">{formatUnit(b.preferred_unit_symbol)}</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-[10px] font-medium text-gray-300 dark:text-dark-muted/20 italic tracking-tight">undefined</span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 dark:text-dark-muted">
                    <div className="flex flex-wrap gap-1">
                      {b.aliases.length > 0 ? b.aliases.map((a, i) => (
                        <span key={i} className="text-[10px] bg-gray-50 dark:bg-dark-bg/50 border border-gray-100 dark:border-dark-border px-1.5 py-0.5 rounded text-gray-400 dark:text-dark-muted">
                          {a}
                        </span>
                      )) : <span className="italic opacity-30 text-[10px]">No aliases defined</span>}
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-right flex items-center justify-end space-x-1">
                    <button 
                      onClick={() => handleEditInfo(b)}
                      className={`p-2 rounded-lg transition-all ${b.info ? 'text-blue-600 bg-blue-50 dark:bg-blue-900/20' : 'text-gray-300 hover:text-gray-500 hover:bg-gray-100 dark:hover:bg-dark-bg'}`}
                      title={b.info ? t('common.edit') : t('biomarker_catalog.add_metric')}
                    >
                      <Edit3 className="w-4 h-4" />
                    </button>
                    <button 
                      onClick={() => handleSingleDelete(b)}
                      className="p-2 text-gray-300 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/10 rounded-lg transition-all"
                      title={t('biomarker_catalog.delete_biomarker')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
              {filteredBiomarkers.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-6 py-20 text-center">
                    <div className="flex flex-col items-center">
                      <ListTree className="w-12 h-12 text-gray-200 mb-2" />
                      <p className="text-gray-400 font-medium">{t('biomarker_catalog.no_biomarkers')}</p>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      
      <div className="text-center mt-8 mb-12 max-w-4xl mx-auto px-4">
        <p className="text-[10px] text-gray-400 dark:text-dark-muted leading-relaxed">
          This material contains content from LOINC (http://loinc.org). LOINC is copyright © Regenstrief Institute, Inc. and the Logical Observation Identifiers Names and Codes (LOINC) Committee and is available at no cost under the license at http://loinc.org/license. LOINC® is a registered United States trademark of Regenstrief Institute, Inc.
        </p>
      </div>

      {/* Info Viewer Modal */}
      {viewingInfo && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div className="bg-white dark:bg-dark-surface w-full max-w-lg rounded-3xl shadow-2xl overflow-hidden animate-in fade-in zoom-in duration-200">
            <div className="p-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
                  <Info className="w-5 h-5 text-blue-600" />
                </div>
                <div>
                  <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text">{viewingInfo.name}</h3>
                  <p className="text-xs text-gray-400 font-mono">{(viewingInfo as any).coding_system === 'loinc' ? 'LOINC: ' : 'CUSTOM: '}{(viewingInfo as any).code || viewingInfo.slug}</p>
                </div>
              </div>
              <button onClick={() => setViewingInfo(null)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>
            <div className="p-8">
              {viewingInfo.info ? (
                <div className="prose dark:prose-invert max-w-none text-gray-700 dark:text-dark-text leading-relaxed">
                  {viewingInfo.info.includes('</') || viewingInfo.info.includes('<br') ? (
                    <div dangerouslySetInnerHTML={{ __html: viewingInfo.info }} />
                  ) : (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{viewingInfo.info}</ReactMarkdown>
                  )}
                </div>
              ) : (
                <div className="text-center py-10">
                  <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-full flex items-center justify-center mx-auto mb-4">
                    <Info className="w-8 h-8 text-gray-200" />
                  </div>
                  <p className="text-gray-400 font-medium">No information available for this biomarker.</p>
                  <button 
                    onClick={() => { setEditingBiomarker(viewingInfo); setInfoText(''); setViewingInfo(null); }}
                    className="mt-4 text-blue-600 font-bold text-sm hover:underline"
                  >
                    Add Information Now
                  </button>
                </div>
              )}
            </div>
            <div className="p-6 bg-gray-50 dark:bg-dark-bg border-t border-gray-100 dark:border-dark-border flex justify-end space-x-3">
              <button 
                onClick={() => {
                  const b = viewingInfo;
                  setViewingInfo(null);
                  handleSingleDelete(b);
                }}
                className="flex items-center space-x-2 px-4 py-2.5 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/10 rounded-xl font-bold text-sm transition-all"
              >
                <Trash2 className="w-4 h-4" />
                <span>Delete</span>
              </button>
              <button 
                onClick={() => { setEditingBiomarker(viewingInfo); setInfoText(viewingInfo.info || ''); setViewingInfo(null); }}
                className="flex items-center space-x-2 px-6 py-2.5 border border-gray-200 dark:border-dark-border text-gray-700 dark:text-dark-text rounded-xl font-bold text-sm hover:bg-gray-50 dark:hover:bg-dark-surface transition-all shadow-sm active:scale-95"
              >
                <Edit3 className="w-4 h-4" />
                <span>Edit Info</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Editor Modal */}
      {editingBiomarker && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div className="bg-white dark:bg-dark-surface w-full max-w-2xl rounded-3xl shadow-2xl overflow-hidden animate-in fade-in zoom-in duration-200">
            <div className="p-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
                  <Edit3 className="w-5 h-5 text-blue-600" />
                </div>
                <h3 className="text-xl font-bold text-gray-900 dark:text-dark-text">{t('biomarker_catalog.edit_info_title')}: {editingBiomarker.name}</h3>
              </div>
              <button onClick={() => setEditingBiomarker(null)} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>
            <div className="p-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                  <div>
                    <label className="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-2 px-1">{t('biomarker_catalog.preferred_unit')}</label>
                    <UnitSelector
                      units={units}
                      selectedId={preferredUnitId}
                      onSelect={(u) => setPreferredUnitId(u.id)}
                      onUnitsUpdated={setUnits}
                      className="w-full"
                    />
                  </div>
                 <div>
                    <label className="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-2 px-1">{t('biomarker_catalog.min_range')}</label>
                    <input 
                      type="number" step="any" placeholder="e.g. 3.9" 
                      className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                      value={rangeMin} onChange={e => setRangeMin(e.target.value)}
                    />
                 </div>
                 <div>
                    <label className="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-2 px-1">{t('biomarker_catalog.max_range')}</label>
                    <input 
                      type="number" step="any" placeholder="e.g. 5.6" 
                      className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text"
                      value={rangeMax} onChange={e => setRangeMax(e.target.value)}
                    />
                 </div>
              </div>
              
              <div className="mb-6 px-1 flex items-center space-x-2 text-blue-600 dark:text-blue-400">
                <Info className="w-4 h-4" />
                <p className="text-[10px] font-bold uppercase tracking-wider">
                  {t('biomarker_catalog.range_warning')}
                </p>
              </div>

              <label className="block text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 px-1">{t('biomarker_catalog.detailed_info')}</label>
              <RichTextEditor 
                value={infoText} 
                onChange={setInfoText} 
                placeholder={t('biomarker_catalog.info_placeholder')} 
                minHeight="300px"
              />
              <p className="mt-2 text-[10px] text-gray-400 px-1 italic">{t('biomarker_catalog.info_note')}</p>
            </div>
            <div className="p-6 bg-gray-50 dark:bg-dark-bg border-t border-gray-100 dark:border-dark-border flex justify-end space-x-3">
              <button 
                onClick={() => setEditingBiomarker(null)}
                className="px-6 py-2.5 text-gray-500 font-bold text-sm hover:text-gray-700 transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button 
                onClick={handleSaveInfo}
                disabled={isSaving}
                className="flex items-center space-x-2 px-8 py-2.5 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none active:scale-95 disabled:opacity-50"
              >
                {isSaving ? (
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></div>
                ) : (
                  <Save className="w-4 h-4" />
                )}
                <span>{t('biomarker_catalog.save_info')}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default BiomarkerCatalog;