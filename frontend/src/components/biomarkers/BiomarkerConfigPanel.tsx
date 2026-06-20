import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, Box, Save, Plus } from 'lucide-react';
import { UnitSelector } from '../ui/UnitSelector';
import biomarkerService from '../../services/biomarkerService';
import { Biomarker, Unit } from '../../types/biomarker';
import { useAuthStore } from '../../store/slices/authSlice';
import { refreshBiomarkerDefinitions } from '../../hooks/useBiomarkers';

import { useUIStore } from '../../store/slices/uiSlice';

interface BiomarkerConfigPanelProps {
  biomarker: Biomarker;
  units: Unit[];
  isEditable?: boolean;
  onUnitsUpdated?: (units: Unit[]) => void;
  onSuccess?: (updatedBiomarker: Biomarker) => void;
}

export const BiomarkerConfigPanel: React.FC<BiomarkerConfigPanelProps> = ({ 
  biomarker, 
  units, 
  isEditable,
  onUnitsUpdated,
  onSuccess 
}) => {
  const { t } = useTranslation();
  const { user } = useAuthStore();
  const { showConfirmation } = useUIStore();
  
  // Only system admins or tenant admins should edit global definitions
  const hasAdminRole = user?.role === 'SYSTEM_ADMIN' || user?.role === 'ADMIN' || user?.role === 'MANAGER';
  // If isEditable is explicitly provided, use it (but enforce admin role). Otherwise, just rely on admin role.
  const canEdit = isEditable !== undefined ? (isEditable && hasAdminRole) : hasAdminRole;

  const [isSaving, setIsSaving] = useState(false);
  const [formData, setFormData] = useState({
    preferred_unit_id: biomarker.preferred_unit_id || '',
    reference_range_min: biomarker.reference_range_min?.toString() || '',
    reference_range_max: biomarker.reference_range_max?.toString() || '',
    is_telemetry: biomarker.is_telemetry || false
  });

  // Reset form if biomarker changes
  useEffect(() => {
    setFormData({
      preferred_unit_id: biomarker.preferred_unit_id || '',
      reference_range_min: biomarker.reference_range_min?.toString() || '',
      reference_range_max: biomarker.reference_range_max?.toString() || '',
      is_telemetry: biomarker.is_telemetry || false
    });
  }, [biomarker]);

  const hasChanges = 
    formData.preferred_unit_id !== (biomarker.preferred_unit_id || '') ||
    formData.reference_range_min !== (biomarker.reference_range_min?.toString() || '') ||
    formData.reference_range_max !== (biomarker.reference_range_max?.toString() || '') ||
    formData.is_telemetry !== (biomarker.is_telemetry || false);

  const performSave = async () => {
    setIsSaving(true);
    try {
      const updated = await biomarkerService.updateBiomarker(biomarker.id, {
        preferred_unit_id: formData.preferred_unit_id === '' ? null : formData.preferred_unit_id,
        reference_range_min: formData.reference_range_min === '' ? null : parseFloat(formData.reference_range_min),
        reference_range_max: formData.reference_range_max === '' ? null : parseFloat(formData.reference_range_max),
        is_telemetry: formData.is_telemetry
      });
      refreshBiomarkerDefinitions();
      
      if (onSuccess) {
        onSuccess(updated);
      }
    } catch (error) {
      console.error("Failed to update biomarker config", error);
      alert(t('common.error') || "Failed to update configuration");
    } finally {
      setIsSaving(false);
    }
  };

  const handleSave = async () => {
    if (!canEdit) return;
    
    if (formData.is_telemetry !== (biomarker.is_telemetry || false)) {
      showConfirmation({
        title: "Migrate Telemetry Data?",
        message: "Warning: Changing this biomarker's telemetry type will migrate all existing historical data between databases. This could take a while for large datasets. Are you sure you want to continue?",
        confirmLabel: "Yes, Migrate Data",
        cancelLabel: "Cancel",
        confirmVariant: "danger",
        onConfirm: performSave
      });
      return;
    }
    
    await performSave();
  };

  return (
    <div className="bg-white dark:bg-dark-surface p-6 rounded-3xl border border-gray-100 dark:border-dark-border shadow-sm flex flex-col h-full relative overflow-hidden">
      {!canEdit && (
         <div className="absolute top-0 right-0 bg-gray-100 dark:bg-dark-bg px-3 py-1 text-[9px] font-black uppercase tracking-widest text-gray-500 rounded-bl-xl border-b border-l border-gray-200 dark:border-dark-border">
           Read Only
         </div>
      )}

      <div className="flex items-center space-x-3 mb-6">
        <div className="p-2 bg-slate-50 dark:bg-slate-900/30 rounded-xl border border-slate-100 dark:border-slate-800">
          <Box className="w-5 h-5 text-slate-600 dark:text-slate-400" />
        </div>
        <div>
          <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text tracking-tight">Configuration</h3>
          <p className="text-[10px] text-gray-400 uppercase tracking-widest">Global Properties</p>
        </div>
      </div>

      <div className="space-y-5 flex-1">
        <div>
          <label className="block text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1.5 px-1">{t('biomarker_catalog.preferred_unit')}</label>
          <div className={canEdit ? "" : "pointer-events-none opacity-80"}>
            <UnitSelector
              units={units}
              selectedId={formData.preferred_unit_id}
              onSelect={(u) => setFormData(prev => ({ ...prev, preferred_unit_id: u.id }))}
              onUnitsUpdated={onUnitsUpdated || (() => {})}
              className="w-full"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1.5 px-1">{t('biomarker_catalog.min_range')}</label>
            <input 
              type="number" step="any" placeholder="e.g. 3.9" 
              disabled={!canEdit}
              className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm font-bold focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text disabled:opacity-70 disabled:cursor-not-allowed"
              value={formData.reference_range_min} 
              onChange={e => setFormData(prev => ({ ...prev, reference_range_min: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1.5 px-1">{t('biomarker_catalog.max_range')}</label>
            <input 
              type="number" step="any" placeholder="e.g. 5.6" 
              disabled={!canEdit}
              className="w-full px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm font-bold focus:ring-2 focus:ring-blue-500 outline-none transition-all dark:text-dark-text disabled:opacity-70 disabled:cursor-not-allowed"
              value={formData.reference_range_max} 
              onChange={e => setFormData(prev => ({ ...prev, reference_range_max: e.target.value }))}
            />
          </div>
        </div>

        <div className="pt-2">
          <label className={`flex items-center space-x-3 p-4 rounded-xl border transition-colors ${canEdit ? 'hover:border-gray-300 dark:hover:border-gray-700 cursor-pointer bg-gray-50 dark:bg-dark-bg border-gray-200 dark:border-dark-border' : 'bg-gray-50/50 dark:bg-dark-bg/50 border-gray-100 dark:border-dark-border/50 opacity-80 cursor-default'}`}>
            <input
              type="checkbox"
              disabled={!canEdit}
              checked={formData.is_telemetry}
              onChange={e => setFormData(prev => ({ ...prev, is_telemetry: e.target.checked }))}
              className="w-4 h-4 text-indigo-600 rounded border-gray-300 focus:ring-indigo-500 disabled:opacity-50"
            />
            <div className="flex flex-col">
              <span className="text-xs font-bold text-gray-900 dark:text-dark-text flex items-center gap-1.5">
                <Activity className="w-3.5 h-3.5 text-indigo-500" />
                IoT Telemetry Metric
              </span>
              <span className="text-[10px] text-gray-500 leading-tight mt-0.5">Routes continuous high-frequency data to TimescaleDB.</span>
            </div>
          </label>
        </div>
      </div>

      {canEdit && (
        <div className="pt-5 mt-4 border-t border-gray-50 dark:border-dark-border flex justify-end">
          <button 
            onClick={handleSave} 
            disabled={!hasChanges || isSaving} 
            className={`flex items-center space-x-2 px-6 py-2 rounded-xl font-bold text-xs uppercase tracking-widest transition-all ${
              hasChanges 
                ? 'bg-blue-600 text-white shadow-lg shadow-blue-200/50 dark:shadow-none hover:bg-blue-700 active:scale-95' 
                : 'bg-gray-100 dark:bg-dark-bg text-gray-400 dark:text-dark-muted cursor-not-allowed'
            }`}
          >
            {isSaving ? <Activity className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            <span>{isSaving ? t('common.saving') || 'Saving...' : t('common.save') || 'Save'}</span>
          </button>
        </div>
      )}
    </div>
  );
};
