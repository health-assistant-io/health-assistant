import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useUIStore } from '../../store/slices/uiSlice';
import { Search, X, FileText, Activity, Users, Settings, ChevronRight, Pill, Droplet, PersonStanding, ShieldAlert, Syringe, Network } from 'lucide-react';
import api from '../../api/axios';

export function SearchLauncher() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  
  const isSearchLauncherOpen = useUIStore(state => state.isSearchLauncherOpen);
  const setSearchLauncherOpen = useUIStore(state => state.setSearchLauncherOpen);
  const searchMode = useUIStore(state => state.searchMode);
  const setSearchMode = useUIStore(state => state.setSearchMode);
  const pageSearchTerm = useUIStore(state => state.pageSearchTerm);
  const setPageSearchTerm = useUIStore(state => state.setPageSearchTerm);
  const isPageSearchSupported = useUIStore(state => state.isPageSearchSupported);

  const [globalSearchTerm, setGlobalSearchTerm] = useState('');
  const [globalResults, setGlobalResults] = useState<any[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const launcherRef = useRef<HTMLDivElement>(null);

  // Listen for Escape to close
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isSearchLauncherOpen) {
        setSearchLauncherOpen(false);
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isSearchLauncherOpen, setSearchLauncherOpen]);

  // Focus input when opened and auto-switch mode ONLY on open
  useEffect(() => {
    if (isSearchLauncherOpen) {
      // Auto-switch to page mode if supported, else global
      if (isPageSearchSupported) {
        setSearchMode('page');
      } else {
        setSearchMode('global');
      }
      
      setTimeout(() => {
        inputRef.current?.focus();
      }, 50);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSearchLauncherOpen]);

  // Handle click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (launcherRef.current && !launcherRef.current.contains(e.target as Node)) {
        setSearchLauncherOpen(false);
      }
    };
    
    if (isSearchLauncherOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isSearchLauncherOpen, setSearchLauncherOpen]);

  // Perform global search
  useEffect(() => {
    if (searchMode === 'global' && globalSearchTerm.length >= 2) {
      setIsSearching(true);
      const timer = setTimeout(async () => {
      try {
        const { data } = await api.get(`/search?q=${encodeURIComponent(globalSearchTerm)}`);
        setGlobalResults(data?.results || []);
      } catch (error) {
        console.error('Global search error:', error);
        setGlobalResults([]);
      } finally {
          setIsSearching(false);
        }
      }, 300);
      
      return () => clearTimeout(timer);
    } else {
      setGlobalResults([]);
    }
  }, [globalSearchTerm, searchMode]);

  if (!isSearchLauncherOpen) return null;

  const handleGlobalResultClick = (result: any) => {
    setSearchLauncherOpen(false);
    // Navigation logic based on result type
    switch (result.type) {
      case 'patient':
        navigate(`/patients/${result.id}`);
        break;
      case 'examination':
        navigate(`/examinations/${result.id}`);
        break;
      case 'document':
        navigate(`/documents/${result.id}`);
        break;
      case 'event':
        navigate(`/events/${result.id}`);
        break;
      case 'medication':
        navigate(`/medications/details/${result.id}`);
        break;
      case 'biomarker':
        navigate(`/biomarkers/details/${result.id}`);
        break;
      case 'anatomy':
        navigate(`/anatomy/${result.id}`);
        break;
      case 'allergy':
        navigate(`/alerts`);
        break;
      case 'vaccine':
        navigate(`/catalogs?type=vaccine`);
        break;
      case 'concept':
        navigate(`/admin/system/taxonomy`);
        break;
      default:
        break;
    }
  };

  const renderIconForType = (type: string) => {
    switch (type) {
      case 'patient': return <Users className="w-4 h-4 text-blue-500" />;
      case 'examination': return <Activity className="w-4 h-4 text-purple-500" />;
      case 'document': return <FileText className="w-4 h-4 text-green-500" />;
      case 'event': return <Activity className="w-4 h-4 text-orange-500" />;
      case 'medication': return <Pill className="w-4 h-4 text-pink-500" />;
      case 'biomarker': return <Droplet className="w-4 h-4 text-red-500" />;
      case 'anatomy': return <PersonStanding className="w-4 h-4 text-emerald-500" />;
      case 'allergy': return <ShieldAlert className="w-4 h-4 text-amber-500" />;
      case 'vaccine': return <Syringe className="w-4 h-4 text-rose-500" />;
      case 'concept': return <Network className="w-4 h-4 text-slate-500" />;
      default: return <Search className="w-4 h-4 text-gray-500" />;
    }
  };

  return (
    <div 
      className={`fixed inset-0 z-[1000] flex items-start justify-center px-4 transition-all duration-300 ${
        searchMode === 'global' 
          ? 'pt-[10vh] sm:pt-[15vh] pb-20 bg-black/20 dark:bg-black/40 backdrop-blur-sm pointer-events-auto' 
          : 'pt-4 sm:pt-6 pb-4 bg-transparent pointer-events-none'
      }`}
    >
      <div 
        ref={launcherRef}
        className={`w-full max-w-2xl bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border flex flex-col pointer-events-auto transition-all duration-300 ${
          searchMode === 'global' ? 'shadow-2xl overflow-hidden' : 'shadow-lg'
        }`}
      >
        {/* Header Tabs */}
        <div className="flex border-b border-gray-100 dark:border-dark-border px-2 pt-2 bg-gray-50/50 dark:bg-dark-bg/50">
          <button
            onClick={() => isPageSearchSupported && setSearchMode('page')}
            disabled={!isPageSearchSupported}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              searchMode === 'page' 
                ? 'bg-white dark:bg-dark-surface text-blue-600 dark:text-blue-400 border-t border-x border-gray-100 dark:border-dark-border' 
                : 'text-gray-500 dark:text-dark-muted hover:text-gray-700 dark:hover:text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed'
            }`}
          >
            {t('common.current_page', 'Current Page')}
          </button>
          <button
            onClick={() => setSearchMode('global')}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${
              searchMode === 'global' 
                ? 'bg-white dark:bg-dark-surface text-blue-600 dark:text-blue-400 border-t border-x border-gray-100 dark:border-dark-border' 
                : 'text-gray-500 dark:text-dark-muted hover:text-gray-700 dark:hover:text-gray-300'
            }`}
          >
            {t('common.global_search', 'Global Search')}
          </button>
        </div>

        {/* Search Input */}
        <div className={`relative p-4 ${searchMode === 'global' ? 'border-b border-gray-100 dark:border-dark-border' : ''}`}>
          <Search className="absolute left-7 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            ref={inputRef}
            type="text"
            className="w-full pl-12 pr-10 py-3 bg-gray-100 dark:bg-dark-bg border-none rounded-xl text-lg outline-none focus:ring-2 focus:ring-blue-500/50 dark:text-white placeholder-gray-400"
            placeholder={
              searchMode === 'page' 
                ? t('common.search_current_page', 'Search in current page...') 
                : t('common.search_everything', 'Search across all records...')
            }
            value={searchMode === 'page' ? pageSearchTerm : globalSearchTerm}
            onChange={(e) => {
              if (searchMode === 'page') {
                setPageSearchTerm(e.target.value);
              } else {
                setGlobalSearchTerm(e.target.value);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && searchMode === 'page') {
                setSearchLauncherOpen(false);
              }
            }}
          />
          {((searchMode === 'page' && pageSearchTerm) || (searchMode === 'global' && globalSearchTerm)) && (
            <button 
              onClick={() => {
                if (searchMode === 'page') setPageSearchTerm('');
                else setGlobalSearchTerm('');
                inputRef.current?.focus();
              }}
              className="absolute right-7 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Results Area */}
        {searchMode === 'global' && (
          <div className="max-h-[60vh] overflow-y-auto p-2">
            {isSearching ? (
              <div className="p-8 text-center text-gray-400 dark:text-dark-muted flex flex-col items-center">
                <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mb-2"></div>
                <p className="text-sm">{t('common.searching', 'Searching...')}</p>
              </div>
            ) : globalSearchTerm.length < 2 ? (
              <div className="p-8 text-center text-gray-400 dark:text-dark-muted">
                <p className="text-sm">{t('common.type_to_search', 'Type at least 2 characters to search globally')}</p>
              </div>
            ) : globalResults.length === 0 ? (
              <div className="p-8 text-center text-gray-400 dark:text-dark-muted">
                <p className="text-sm">{t('common.no_results', 'No results found')}</p>
              </div>
            ) : (
              <div className="space-y-1">
                {globalResults.map((result) => (
                  <button
                    key={result.id}
                    onClick={() => handleGlobalResultClick(result)}
                    className="w-full flex items-center px-4 py-3 hover:bg-gray-50 dark:hover:bg-dark-bg rounded-xl transition-colors group text-left"
                  >
                    <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-gray-100 dark:bg-dark-bg flex items-center justify-center mr-4 group-hover:bg-white dark:group-hover:bg-dark-surface border border-gray-200 dark:border-dark-border">
                      {renderIconForType(result.type)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-bold text-gray-900 dark:text-white truncate">
                        {result.title}
                      </p>
                      {result.subtitle && (
                        <p className="text-xs text-gray-500 dark:text-dark-muted truncate">
                          {result.subtitle}
                        </p>
                      )}
                    </div>
                    <ChevronRight className="w-4 h-4 text-gray-300 opacity-0 group-hover:opacity-100 transition-opacity" />
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        


        {/* Footer */}
        {searchMode === 'global' && (
          <div className="px-4 py-2 border-t border-gray-100 dark:border-dark-border bg-gray-50 dark:bg-dark-bg text-xs text-gray-400 dark:text-dark-muted flex justify-between items-center">
            <span>
              {t('common.search_shortcuts', 'Navigation and search')}
            </span>
            <span className="flex items-center gap-2">
               <kbd className="px-1.5 py-0.5 rounded bg-gray-200 dark:bg-dark-border font-sans font-medium text-[10px]">esc</kbd> {t('common.to_close', 'to close')}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}