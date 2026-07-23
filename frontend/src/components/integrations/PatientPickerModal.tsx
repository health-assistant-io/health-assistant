import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Search, Check, User, AlertCircle } from 'lucide-react';
import { toast } from 'react-toastify';
import { integrationService } from '../../services/integrationService';
import { Portal } from '../ui/Portal';

interface RemotePatientMatch {
  id: string;
  name: string;
  mrn?: string | null;
  birth_date?: string | null;
  gender?: string | null;
}

interface FindPatientResult {
  query?: string | null;
  identifier?: string | null;
  auto_suggested?: string | boolean;
  matches: RemotePatientMatch[];
  current?: string | null;
}

interface Props {
  integrationId: string;
  patientId: string;
  onClose: () => void;
  /** Called after a patient is selected so the parent can refresh details. */
  onSelected: () => void;
}

/**
 * Interactive remote-FHIR-patient picker for the ``fhir_server`` integration.
 *
 * Triggered by the ``find_patient`` custom action (which declares
 * ``modal: "patient_picker"``). Searches the remote server via the
 * ``find_patient`` action (by name, or by MRN auto-suggested from the
 * local patient) and lets the user click a match to link it — stored as
 * ``remote_patient_id`` via the ``select_patient`` action.
 */
const PatientPickerModal: React.FC<Props> = ({ integrationId, patientId, onClose, onSelected }) => {
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<RemotePatientMatch[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [autoSuggested, setAutoSuggested] = useState<string | boolean>(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectingId, setSelectingId] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runSearch = useCallback(
    async (searchQuery: string) => {
      setLoading(true);
      setError(null);
      try {
        const input = searchQuery.trim() ? { query: searchQuery.trim() } : {};
        const result: FindPatientResult = await integrationService.executeAction(
          integrationId,
          patientId,
          'find_patient',
          input,
        );
        setMatches(result.matches || []);
        setCurrent(result.current ?? null);
        setAutoSuggested(result.auto_suggested ?? false);
      } catch (err: any) {
        setError(err.response?.data?.detail || 'Search failed. Is the server reachable?');
        setMatches([]);
      } finally {
        setLoading(false);
      }
    },
    [integrationId, patientId],
  );

  // Auto-search on open (no query → MRN auto-suggest from the local patient).
  useEffect(() => {
    runSearch('');
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleQueryChange = (value: string) => {
    setQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => runSearch(value), 400);
  };

  const handleSelect = async (match: RemotePatientMatch) => {
    setSelectingId(match.id);
    try {
      await integrationService.executeAction(integrationId, patientId, 'select_patient', {
        patient_id: match.id,
      });
      toast.success(`Linked remote patient: ${match.name}`);
      onSelected();
      onClose();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to select patient');
    } finally {
      setSelectingId(null);
    }
  };

  const autoSuggestLabel =
    autoSuggested === 'MRN'
      ? 'Auto-searched by the local patient’s MRN'
      : autoSuggested === 'name'
        ? 'Auto-searched by the local patient’s name'
        : null;

  return (
    <Portal>
      <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
        <div className="bg-white dark:bg-dark-surface w-full max-w-2xl rounded-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[90vh] z-[10000]">
          <div className="p-6 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
            <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text">
              <User className="w-5 h-5 mr-2 text-blue-500" />
              Find Patient on FHIR Server
            </h3>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
            >
              <X className="w-6 h-6" />
            </button>
          </div>

          <div className="p-6 overflow-y-auto flex-1">
            {/* Search */}
            <div className="relative mb-3">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                type="text"
                value={query}
                onChange={(e) => handleQueryChange(e.target.value)}
                placeholder="Search by name (e.g. Smith) or clear to auto-match by MRN"
                autoFocus
                className="w-full rounded-xl border border-gray-200 dark:border-dark-border pl-10 pr-4 py-2.5 bg-white dark:bg-dark-bg text-gray-900 dark:text-dark-text focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
              />
            </div>

            {autoSuggestLabel && !query && (
              <p className="text-xs text-blue-600 dark:text-blue-400 mb-3 flex items-center gap-1">
                <Check className="w-3 h-3" /> {autoSuggestLabel}
              </p>
            )}

            {current && (
              <p className="text-xs text-gray-500 dark:text-dark-muted mb-3">
                Currently linked: <span className="font-mono font-medium">{current}</span>
              </p>
            )}

            {error && (
              <div className="p-3 mb-3 rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 flex items-start gap-2">
                <AlertCircle className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
                <p className="text-xs text-amber-700 dark:text-amber-300">{error}</p>
              </div>
            )}

            {/* Results */}
            {loading ? (
              <div className="py-10 text-center text-sm text-gray-400">Searching…</div>
            ) : matches.length === 0 ? (
              <div className="py-10 text-center text-sm text-gray-400">
                No matches found. Try a different name or MRN.
              </div>
            ) : (
              <div className="space-y-2">
                {matches.map((m) => {
                  const isSelected = m.id === current;
                  const isSelecting = m.id === selectingId;
                  return (
                    <button
                      type="button"
                      key={m.id}
                      onClick={() => handleSelect(m)}
                      disabled={isSelecting}
                      className={`w-full flex items-center gap-3 text-left rounded-xl border p-3 transition-colors disabled:opacity-60 ${
                        isSelected
                          ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 ring-2 ring-blue-500/20'
                          : 'border-gray-200 dark:border-dark-border hover:border-blue-400 hover:bg-gray-50 dark:hover:bg-dark-bg bg-white dark:bg-dark-surface'
                      }`}
                    >
                      <div
                        className={`flex-shrink-0 w-9 h-9 rounded-full flex items-center justify-center ${
                          isSelected ? 'bg-blue-600 text-white' : 'bg-gray-100 dark:bg-dark-bg text-gray-500'
                        }`}
                      >
                        {isSelected ? <Check className="w-4 h-4" /> : <User className="w-4 h-4" />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-semibold text-gray-900 dark:text-dark-text truncate">
                          {m.name}
                        </div>
                        <div className="text-xs text-gray-500 dark:text-dark-muted flex flex-wrap gap-x-3">
                          {m.mrn && <span>MRN: {m.mrn}</span>}
                          {m.birth_date && <span>DOB: {m.birth_date}</span>}
                          {m.gender && <span className="capitalize">{m.gender}</span>}
                        </div>
                      </div>
                      <div className="flex-shrink-0 text-right">
                        <div className="text-xs font-mono text-gray-400">{m.id}</div>
                        <div className="text-[10px] uppercase tracking-wide text-blue-600 dark:text-blue-400 mt-0.5">
                          {isSelecting ? 'Linking…' : isSelected ? 'Linked' : 'Select'}
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="p-6 bg-gray-50 dark:bg-dark-border/30 flex items-center justify-between">
            <p className="text-xs text-gray-400">
              Selecting a patient links it to this integration’s sync target.
            </p>
            <button
              onClick={onClose}
              className="px-6 py-2 text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-xl transition-all shadow-md shadow-blue-200/50 dark:shadow-none active:scale-95"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </Portal>
  );
};

export default PatientPickerModal;
