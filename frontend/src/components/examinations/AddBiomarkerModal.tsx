import React, { useState, useEffect } from 'react';
import { X, Search, Plus, Save, Activity, Info, FlaskConical, ChevronDown, Check } from 'lucide-react';
import { AIAssistButton } from '../ui/AIAssistButton';
import { UnitSelector } from '../ui/UnitSelector';
import biomarkerService from '../../services/biomarkerService';
import { createObservation } from '../../services/fhirService';
import { Biomarker, Unit } from '../../types/biomarker';
import { formatUnit } from '../../utils/biomarkerUtils';
import { CreateBiomarkerModal } from './CreateBiomarkerModal';
import { useTranslation } from 'react-i18next';
import { filterBiomarkers, matchBiomarker } from '../../utils/searchUtils';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  examinationId: string;
  onSuccess: () => void;
}

export const AddBiomarkerModal: React.FC<Props> = ({ 
  isOpen, 
  onClose, 
  patientId, 
  examinationId,
  onSuccess 
}) => {
  const { t } = useTranslation();
  const [searchTerm, setSearchTerm] = useState('');
  const [catalogResults, setCatalogResults] = useState<Biomarker[]>([]);
  const [selectedBiomarker, setSelectedBiomarker] = useState<Biomarker | null>(null);
  const [units, setUnits] = useState<Unit[]>([]);
  const [loading, setLoading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);

  const [formData, setFormData] = useState({
    value: '',
    unit: '',
    interpretation: 'normal',
    note: ''
  });

  useEffect(() => {
    if (isOpen) {
      biomarkerService.getUnits().then(setUnits);
    }
  }, [isOpen]);

  useEffect(() => {
    if (searchTerm.length > 1 && !selectedBiomarker) {
      const delayDebounceFn = setTimeout(async () => {
        setSearching(true);
        try {
          const all = await biomarkerService.getAllBiomarkers();
          const filtered = filterBiomarkers(all, searchTerm);
          setCatalogResults(filtered.slice(0, 10));
        } catch (err) {
          console.error("Failed to search biomarkers", err);
        } finally {
          setSearching(false);
        }
      }, 300);
      return () => clearTimeout(delayDebounceFn);
    } else {
      setCatalogResults([]);
    }
  }, [searchTerm, selectedBiomarker]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedBiomarker || !formData.value) return;

    setLoading(true);
    try {
      // Construct FHIR Observation
      const observation = {
        patient_id: patientId,
        examination_id: examinationId,
        biomarker_id: selectedBiomarker.id,
        status: 'final',
        category: [{
          coding: [{
            system: 'http://terminology.hl7.org/CodeSystem/observation-category',
            code: 'laboratory',
            display: 'Laboratory'
          }]
        }],
        code: {
          coding: [{
            system: selectedBiomarker.coding_system === 'custom' ? 'urn:uuid:health-assistant:custom-biomarker' : selectedBiomarker.coding_system === 'snomed' ? 'http://snomed.info/sct' : 'http://loinc.org',
            code: selectedBiomarker.code || selectedBiomarker.slug,
            display: selectedBiomarker.name
          }],
          text: selectedBiomarker.name
        },
        valueQuantity: {
          value: parseFloat(formData.value),
          unit: formData.unit || selectedBiomarker.preferred_unit_symbol,
          system: 'http://unitsofmeasure.org',
          code: formData.unit || selectedBiomarker.preferred_unit_symbol
        },
        interpretation: [{
          coding: [{
            system: 'http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation',
            code: formData.interpretation === 'normal' ? 'N' : formData.interpretation === 'high' ? 'H' : 'L',
            display: formData.interpretation.toUpperCase()
          }]
        }],
        note: formData.note ? [{ text: formData.note }] : []
      };

      await createObservation(observation as any);
      onSuccess();
      onClose();
      // Reset form
      setSelectedBiomarker(null);
      setSearchTerm('');
      setFormData({ value: '', unit: '', interpretation: 'normal', note: '' });
    } catch (err) {
      console.error("Failed to add biomarker", err);
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-white dark:bg-dark-surface w-full max-w-xl rounded-3xl shadow-2xl border border-gray-100 dark:border-dark-border overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="px-8 py-6 border-b border-gray-50 dark:border-dark-border flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
              <FlaskConical className="w-6 h-6 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-dark-text">{t('examination_detail.add_biomarker.title')}</h2>
              <p className="text-[10px] text-gray-400 font-black uppercase tracking-widest mt-0.5">{t('examination_detail.add_biomarker.manual_entry')}</p>
            </div>
          </div>
          <div className="flex items-center space-x-4">
            <AIAssistButton 
              taskType="fill_biomarker_form" 
              context={{ patientId, examinationId }} 
              onSuggestedData={async (data) => {
                console.log("AI Suggested Data:", data);
                
                // 1. Fill form data immediately
                setFormData(prev => ({
                  ...prev,
                  value: (data.value !== undefined && data.value !== null) ? data.value.toString() : prev.value,
                  unit: data.unit || prev.unit,
                  interpretation: data.interpretation || prev.interpretation,
                  note: data.note || prev.note
                }));

                // 2. Handle biomarker matching separately
                if (data.biomarker_name && !selectedBiomarker) {
                  try {
                    setSearching(true);
                    const all = await biomarkerService.getAllBiomarkers();
                    const match = all.find(b => matchBiomarker(b, data.biomarker_name));
                    
                    if (match) {
                      setSelectedBiomarker(match);
                      setSearchTerm(match.name);
                      // Update unit if match found and AI didn't provide one
                      if (!data.unit) {
                        setFormData(prev => ({ ...prev, unit: match.preferred_unit_symbol || prev.unit }));
                      }
                    } else {
                      setSearchTerm(data.biomarker_name);
                    }
                  } catch (err) {
                    console.error("Failed to auto-select biomarker", err);
                  } finally {
                    setSearching(false);
                  }
                }
              }}
            />
            <button onClick={onClose} className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded-full transition-colors">
              <X className="w-5 h-5 text-gray-400" />
            </button>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-8 space-y-8">
          {/* Biomarker Selection */}
          <div className="space-y-4">
            <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
              <Search className="w-3 h-3 mr-2" />
              {t('examination_detail.add_biomarker.search_catalog')}
            </label>
            <div className="relative">
              <input
                type="text"
                placeholder={t('examination_detail.add_biomarker.search_placeholder')}
                className="w-full px-6 py-4 bg-gray-50 dark:bg-dark-bg border-none rounded-2xl text-gray-900 dark:text-dark-text placeholder-gray-400 focus:ring-2 focus:ring-blue-500/20 transition-all font-medium"
                value={searchTerm}
                onChange={(e) => {
                  setSearchTerm(e.target.value);
                  if (selectedBiomarker) setSelectedBiomarker(null);
                }}
                autoFocus={!selectedBiomarker}
              />
              {searching && (
                <div className="absolute right-4 top-4">
                  <div className="animate-spin rounded-full h-5 w-5 border-2 border-blue-500 border-t-transparent" />
                </div>
              )}
            </div>

            {catalogResults.length > 0 && !selectedBiomarker && (
              <div className="bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-2xl shadow-xl overflow-hidden animate-in slide-in-from-top-2 z-[510] relative">
                {catalogResults.map(item => (
                  <button
                    key={item.id}
                    type="button"
                    className="w-full px-6 py-4 text-left hover:bg-blue-50 dark:hover:bg-blue-900/10 transition-colors border-b border-gray-50 dark:border-dark-border last:border-0"
                    onClick={() => {
                      setSelectedBiomarker(item);
                      setSearchTerm(item.name);
                      setFormData({ ...formData, unit: item.preferred_unit_symbol || '' });
                      setCatalogResults([]);
                    }}
                  >
                    <p className="font-bold text-gray-900 dark:text-dark-text">{item.name}</p>
                    <p className="text-[10px] text-gray-400 uppercase font-black tracking-tighter mt-0.5">{item.category || t('examination_detail.repository.medical_file')}</p>
                  </button>
                ))}
                
                <button
                  type="button"
                  onClick={() => setIsCreateModalOpen(true)}
                  className="w-full text-left px-6 py-5 bg-blue-50/50 dark:bg-blue-900/10 hover:bg-blue-50 dark:hover:bg-blue-900/20 flex items-center space-x-3 text-blue-600 border-t border-gray-50 dark:border-dark-border"
                >
                  <div className="p-2 bg-blue-600 text-white rounded-xl">
                    <Plus className="w-4 h-4" />
                  </div>
                  <div>
                    <p className="text-sm font-bold italic">{t('examination_detail.add_biomarker.not_in_catalog', { term: searchTerm })}</p>
                    <p className="text-[10px] font-bold uppercase tracking-widest">{t('examination_detail.add_biomarker.create_new_definition')}</p>
                  </div>
                </button>
              </div>
            )}

            {searchTerm.length > 2 && catalogResults.length === 0 && !searching && !selectedBiomarker && (
              <button
                type="button"
                onClick={() => setIsCreateModalOpen(true)}
                className="w-full text-left px-6 py-5 bg-blue-50/50 dark:bg-blue-900/10 hover:bg-blue-50 dark:hover:bg-blue-900/20 flex items-center space-x-3 text-blue-600 rounded-2xl border border-dashed border-blue-200"
              >
                <div className="p-2 bg-blue-600 text-white rounded-xl">
                  <Plus className="w-4 h-4" />
                </div>
                <div>
                  <p className="text-sm font-bold italic">{t('examination_detail.add_biomarker.not_found', { term: searchTerm })}</p>
                  <p className="text-[10px] font-bold uppercase tracking-widest">{t('examination_detail.add_biomarker.create_new_definition')}</p>
                </div>
              </button>
            )}

            {selectedBiomarker && (
              <div className="p-4 bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 rounded-2xl animate-in zoom-in-95 duration-200">
                <div className="flex items-start justify-between">
                  <div className="flex items-center space-x-3">
                    <div className="p-2 bg-blue-600 text-white rounded-lg">
                       <Activity className="w-4 h-4" />
                    </div>
                    <div>
                      <h4 className="font-bold text-blue-900 dark:text-blue-300">{selectedBiomarker.name}</h4>
                      <p className="text-[10px] text-blue-600 dark:text-blue-400 uppercase font-black tracking-widest">{selectedBiomarker.slug}</p>
                    </div>
                  </div>
                  <button 
                    type="button" 
                    onClick={() => setSelectedBiomarker(null)}
                    className="text-[10px] font-bold text-blue-600 dark:text-blue-400 uppercase tracking-widest hover:underline"
                  >
                    {t('examination_detail.add_biomarker.change')}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Form Fields */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">{t('examination_detail.add_biomarker.value')}</label>
              <input
                type="number"
                step="any"
                placeholder="0.00"
                className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border-none rounded-xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 font-bold"
                value={formData.value}
                onChange={e => setFormData({...formData, value: e.target.value})}
                required
              />
            </div>

            <div className="space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">{t('examination_detail.add_biomarker.unit')}</label>
              <UnitSelector
                units={units}
                selectedSymbol={formData.unit}
                onSelect={(u) => setFormData(prev => ({ ...prev, unit: u.symbol }))}
                onUnitsUpdated={setUnits}
                placeholder={t('examination_detail.add_biomarker.select_unit')}
              />
            </div>

            <div className="col-span-1 md:col-span-2 space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1">{t('examination_detail.add_biomarker.interpretation')}</label>
              <div className="grid grid-cols-3 gap-3">
                {['low', 'normal', 'high'].map(type => (
                  <button
                    key={type}
                    type="button"
                    onClick={() => setFormData({...formData, interpretation: type})}
                    className={`py-3 rounded-xl text-xs font-black uppercase tracking-widest transition-all border-2 ${
                      formData.interpretation === type 
                        ? type === 'normal' 
                          ? 'bg-blue-600 border-blue-600 text-white shadow-lg shadow-blue-500/20' 
                          : 'bg-red-600 border-red-600 text-white shadow-lg shadow-red-500/20'
                        : 'bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border text-gray-400 hover:border-gray-200'
                    }`}
                  >
                    {t(`examination_detail.add_biomarker.${type}`)}
                  </button>
                ))}
              </div>
            </div>

            <div className="col-span-1 md:col-span-2 space-y-3">
              <label className="text-[10px] font-black text-gray-400 uppercase tracking-widest px-1 flex items-center">
                <Info className="w-3 h-3 mr-2" />
                {t('examination_detail.add_biomarker.observations')}
              </label>
              <textarea
                rows={3}
                className="w-full px-4 py-4 bg-gray-50 dark:bg-dark-bg border-none rounded-2xl text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 resize-none text-sm"
                placeholder={t('examination_detail.add_biomarker.observations_placeholder')}
                value={formData.note}
                onChange={e => setFormData({...formData, note: e.target.value})}
              />
            </div>
          </div>
        </form>

        {/* Footer */}
        <div className="px-8 py-6 bg-gray-50 dark:bg-dark-bg/50 border-t border-gray-50 dark:border-dark-border flex items-center justify-end space-x-4">
          <button
            type="button"
            onClick={onClose}
            className="px-6 py-2.5 text-sm font-bold text-gray-500 hover:text-gray-700 dark:text-dark-muted transition-colors uppercase tracking-widest"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || !selectedBiomarker || !formData.value}
            className="px-8 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl font-bold text-sm shadow-lg shadow-blue-500/20 transition-all flex items-center space-x-2 uppercase tracking-widest"
          >
            {loading ? (
              <div className="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            <span>{t('examination_detail.add_biomarker.add_result')}</span>
          </button>
        </div>
      </div>

      <CreateBiomarkerModal 
        isOpen={isCreateModalOpen} 
        onClose={() => setIsCreateModalOpen(false)} 
        initialName={searchTerm}
        onSuccess={(bio) => {
          setSelectedBiomarker(bio);
          setSearchTerm(bio.name);
          setFormData({ ...formData, unit: bio.preferred_unit_symbol || '' });
        }}
      />
    </div>
  );
};
