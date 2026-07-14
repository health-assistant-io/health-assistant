import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, Plus, AlertCircle, History } from 'lucide-react';
import { DatePicker } from '../ui/DatePicker';
import { FormModal } from '../ui/FormModal';
import { 
  searchAllergyCatalog, 
  AllergyCatalogEntry, 
  addCustomAllergen,
  addPatientAllergy,
  updatePatientAllergy,
  AllergyIntolerance
} from '../../services/allergyService';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  patientId: string;
  allergy?: AllergyIntolerance;
  onSuccess: () => void;
}

export const AllergyModal: React.FC<Props> = ({ isOpen, onClose, patientId, allergy, onSuccess }) => {
  const { t } = useTranslation();
  const [searchTerm, setSearchTerm] = useState('');
  const [catalogResults, setCatalogResults] = useState<AllergyCatalogEntry[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  
  // Selection / Form state
  const [selectedCatalogItem, setSelectedCatalogItem] = useState<AllergyCatalogEntry | null>(null);
  const [customCategory, setCustomCategory] = useState<'food' | 'medication' | 'environment' | 'biologic'>('other' as any);
  const [isAddingNew, setIsAddingNew] = useState(false);
  const [clinicalStatus, setClinicalStatus] = useState<'active' | 'inactive' | 'resolved'>('active');
  const [criticality, setCriticality] = useState<'low' | 'high' | 'unable-to-assess'>('low');
  const [onsetDate, setOnsetDate] = useState('');
  const [resolvedDate, setResolvedDate] = useState('');
  const [lastOccurrence, setLastOccurrence] = useState('');
  const [note, setNote] = useState('');
  const [reactions, setReactions] = useState<Array<{ manifestation: string; severity: string }>>([]);
  const [newReaction, setNewReaction] = useState({ manifestation: '', severity: 'mild' });

  useEffect(() => {
    if (isOpen) {
      if (allergy) {
        setSearchTerm(allergy.code.text);
        setClinicalStatus(allergy.clinical_status);
        setCriticality(allergy.criticality || 'low');
        setOnsetDate(allergy.onset_date ? allergy.onset_date.split('T')[0] : '');
        setResolvedDate(allergy.resolved_date ? allergy.resolved_date.split('T')[0] : '');
        setLastOccurrence(allergy.last_occurrence ? allergy.last_occurrence.split('T')[0] : '');
        setNote(allergy.note || '');
        setReactions(allergy.reactions || []);
        setSelectedCatalogItem({ name: allergy.code.text, category: allergy.category } as any);
      } else {
        resetForm();
      }
    }
  }, [isOpen, allergy]);

  useEffect(() => {
    if (searchTerm.length >= 2 && !allergy) {
      const delay = setTimeout(() => {
        setIsSearching(true);
        searchAllergyCatalog(searchTerm).then(results => {
          setCatalogResults(results);
          setIsSearching(false);
        });
      }, 300);
      return () => clearTimeout(delay);
    } else {
      setCatalogResults([]);
    }
  }, [searchTerm, allergy]);

  const resetForm = () => {
    setSearchTerm('');
    setSelectedCatalogItem(null);
    setIsAddingNew(false);
    setCustomCategory('other' as any);
    setClinicalStatus('active');
    setCriticality('low');
    setOnsetDate('');
    setResolvedDate('');
    setLastOccurrence('');
    setNote('');
    setReactions([]);
  };

  const handleAddReaction = () => {
    if (!newReaction.manifestation) return;
    setReactions([...reactions, { ...newReaction }]);
    setNewReaction({ manifestation: '', severity: 'mild' });
  };

  const removeReaction = (index: number) => {
    setReactions(reactions.filter((_, i) => i !== index));
  };

  const handleSubmit = async () => {
    setSubmitting(true);

    try {
      let catalogId = selectedCatalogItem?.id;
      let allergenName = searchTerm;
      let allergenCategory = selectedCatalogItem?.category || 'other';

      // If it's a completely new allergen
      if (isAddingNew) {
        const newCat = await addCustomAllergen(searchTerm, customCategory, 'User defined allergen');
        catalogId = newCat.id;
        allergenName = newCat.name;
        allergenCategory = newCat.category;
      }

      const payload: any = {
        clinical_status: clinicalStatus,
        criticality: criticality,
        category: allergenCategory,
        code: {
          text: allergenName,
          catalog_id: catalogId
        },
        onset_date: onsetDate ? new Date(onsetDate).toISOString() : null,
        resolved_date: resolvedDate ? new Date(resolvedDate).toISOString() : null,
        last_occurrence: lastOccurrence ? new Date(lastOccurrence).toISOString() : null,
        note: note,
        reactions: reactions
      };

      if (allergy) {
        await updatePatientAllergy(allergy.id, payload);
      } else {
        await addPatientAllergy(patientId, payload);
      }

      onSuccess();
      onClose();
    } catch (err) {
      console.error("Failed to save allergy record", err);
      alert("Error saving record.");
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  const isEditing = !!allergy;

  return (
    <FormModal
      isOpen={isOpen}
      onClose={onClose}
      title={isEditing ? t('allergies.modal.update_title') : t('allergies.modal.new_title')}
      icon={
        <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-lg">
          <AlertCircle className="w-5 h-5 text-blue-600" />
        </div>
      }
      onSubmit={handleSubmit}
      submitting={submitting}
      submitDisabled={!searchTerm}
      submitLabel={isEditing ? t('allergies.modal.update_record') : t('allergies.modal.save_allergy')}
      cancelLabel={t('common.cancel')}
      bodyClassName="p-6 space-y-8"
    >
      {/* Substance Selection */}
      <section className="space-y-4">
        <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center">
          <Search className="w-3 h-3 mr-2" />
          {t('allergies.modal.allergen_info')}
        </h3>
        <div className="relative">
          <div className="flex gap-2">
            <input 
              type="text"
              disabled={isEditing || isAddingNew}
              required
              placeholder={t('allergies.modal.search_placeholder')}
              className="flex-1 px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none transition-all disabled:opacity-50 font-medium"
              value={searchTerm}
              onChange={(e) => {
                setSearchTerm(e.target.value);
                setSelectedCatalogItem(null);
              }}
            />
          </div>

          {searchTerm.length >= 2 && !selectedCatalogItem && !isAddingNew && !isEditing && (
            <div className="absolute top-full left-0 right-0 mt-2 bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-xl shadow-xl z-50 overflow-hidden animate-in fade-in slide-in-from-top-1">
              {isSearching ? (
                <div className="p-4 text-center text-gray-400 text-sm">{t('allergies.modal.searching')}</div>
              ) : (
                <>
                  {catalogResults.map(item => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => { setSelectedCatalogItem(item); setSearchTerm(item.name); }}
                      className="w-full text-left px-4 py-3 hover:bg-gray-50 dark:hover:bg-dark-bg border-b border-gray-50 dark:border-dark-border last:border-0 flex justify-between items-center group"
                    >
                      <div>
                        <p className="font-bold text-sm text-gray-900 dark:text-dark-text group-hover:text-blue-600">{item.name}</p>
                        <p className="text-[10px] text-gray-400 uppercase font-bold">{item.category}</p>
                      </div>
                      <Plus className="w-4 h-4 text-gray-300 group-hover:text-blue-500" />
                    </button>
                  ))}
                  
                  <button
                    type="button"
                    onClick={() => setIsAddingNew(true)}
                    className="w-full text-left px-4 py-4 bg-blue-50/50 dark:bg-blue-900/10 hover:bg-blue-50 dark:hover:bg-blue-900/20 flex items-center space-x-3 text-blue-600"
                  >
                    <div className="p-1.5 bg-blue-600 text-white rounded-lg">
                      <Plus className="w-4 h-4" />
                    </div>
                    <div>
                      <p className="text-sm font-bold italic">"{searchTerm}" {t('allergies.modal.not_found')}</p>
                      <p className="text-[10px] font-bold uppercase">{t('allergies.modal.add_custom')}</p>
                    </div>
                  </button>
                </>
              )}
            </div>
          )}

          {isAddingNew && (
            <div className="mt-4 p-4 bg-blue-50/30 dark:bg-blue-900/10 rounded-2xl border border-blue-100/50 dark:border-blue-900/30 animate-in zoom-in-95">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-[10px] font-bold text-blue-600 uppercase tracking-widest">{t('allergies.modal.define_category')}</h4>
                <button
                  type="button"
                  onClick={() => { setIsAddingNew(false); setSearchTerm(''); }}
                  className="text-xs text-gray-400 hover:text-gray-600"
                >
                  {t('common.cancel')}
                </button>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {(['food', 'medication', 'environment', 'biologic'] as const).map(cat => (
                  <button
                    key={cat}
                    type="button"
                    onClick={() => setCustomCategory(cat)}
                    className={`px-3 py-2 rounded-xl text-[10px] font-bold uppercase border transition-all ${customCategory === cat ? 'bg-blue-600 border-blue-600 text-white' : 'bg-white dark:bg-dark-surface border-gray-200 dark:border-dark-border text-gray-500 hover:bg-gray-50'}`}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Clinical Status */}
      <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="space-y-3">
          <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">{t('allergies.modal.clinical_status')}</h3>
          <div className="flex bg-gray-100 dark:bg-dark-bg p-1 rounded-xl">
            {(['active', 'resolved'] as const).map(s => (
              <button
                key={s}
                type="button"
                onClick={() => setClinicalStatus(s)}
                className={`flex-1 py-2 text-xs font-bold rounded-lg transition-all ${clinicalStatus === s ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-400 dark:text-dark-muted'}`}
              >
                {s.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        <div className="space-y-3">
          <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">{t('allergies.modal.criticality')}</h3>
          <div className="flex bg-gray-100 dark:bg-dark-bg p-1 rounded-xl">
            {(['low', 'high'] as const).map(c => (
              <button
                key={c}
                type="button"
                onClick={() => setCriticality(c)}
                className={`flex-1 py-2 text-xs font-bold rounded-lg transition-all ${criticality === c ? (c === 'high' ? 'bg-red-600 text-white' : 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm') : 'text-gray-400 dark:text-dark-muted'}`}
              >
                {c.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* Timeline Section */}
      <section className="space-y-4">
        <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest flex items-center">
          <History className="w-3 h-3 mr-2" />
          {t('allergies.clinical_timeline')}
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="block text-[10px] font-bold text-gray-500 uppercase mb-1">{t('allergies.modal.onset_date')}</label>
            <DatePicker 
              value={onsetDate}
              onChange={setOnsetDate}
            />
          </div>
          <div>
            <label className="block text-[10px] font-bold text-gray-500 uppercase mb-1">{t('allergies.modal.resolved_date')}</label>
            <DatePicker 
              disabled={clinicalStatus === 'active'}
              value={resolvedDate}
              onChange={setResolvedDate}
            />
          </div>
          <div>
            <label className="block text-[10px] font-bold text-gray-500 uppercase mb-1">{t('allergies.modal.last_occurrence')}</label>
            <DatePicker 
              value={lastOccurrence}
              onChange={setLastOccurrence}
            />
          </div>
        </div>
      </section>

      {/* Reaction Episodes */}
      <section className="space-y-4">
        <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">{t('allergies.modal.reaction_episodes')}</h3>
        <div className="bg-gray-50 dark:bg-dark-bg/30 p-4 rounded-2xl space-y-4 border border-gray-100 dark:border-dark-border">
          <div className="flex gap-3">
            <input 
              type="text"
              placeholder={t('allergies.modal.manifestation_placeholder')}
              className="flex-1 px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
              value={newReaction.manifestation}
              onChange={(e) => setNewReaction({...newReaction, manifestation: e.target.value})}
            />
            <select 
              className="px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm font-bold focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
              value={newReaction.severity}
              onChange={(e) => setNewReaction({...newReaction, severity: e.target.value})}
            >
              <option value="mild">MILD</option>
              <option value="moderate">MODERATE</option>
              <option value="severe">SEVERE</option>
            </select>
            <button 
              type="button"
              onClick={handleAddReaction}
              className="p-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 shadow-md"
            >
              <Plus className="w-5 h-5" />
            </button>
          </div>

          <div className="flex flex-wrap gap-2">
            {reactions.map((r, i) => (
              <div key={i} className="flex items-center space-x-2 bg-white dark:bg-dark-surface px-3 py-1.5 rounded-full border border-gray-100 dark:border-dark-border text-sm font-medium shadow-sm">
                <span className={`w-2 h-2 rounded-full ${r.severity === 'severe' ? 'bg-red-500' : (r.severity === 'moderate' ? 'bg-yellow-500' : 'bg-blue-500')}`} />
                <span className="text-gray-900 dark:text-dark-text">{r.manifestation}</span>
                <button type="button" onClick={() => removeReaction(i)} className="text-gray-400 hover:text-red-500 ml-1" aria-label={`Remove ${r.manifestation}`}>
                  ✕
                </button>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Notes */}
      <section className="space-y-3">
        <h3 className="text-xs font-bold text-gray-400 uppercase tracking-widest">{t('allergies.clinical_notes')}</h3>
        <textarea 
          rows={3}
          placeholder={t('allergies.modal.notes_placeholder')}
          className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </section>
    </FormModal>
  );
};
