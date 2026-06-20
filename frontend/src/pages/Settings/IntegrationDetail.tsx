import React, { useState, useEffect, useMemo } from 'react';
import { useParams, useNavigate, Link, useSearchParams } from 'react-router-dom';
import { integrationService, CustomAction } from '../../services/integrationService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';
import { Activity, ArrowLeft, RefreshCw, Layers, Database, Clock, Settings, Trash2, CheckCircle, XCircle, Edit2, Zap, FileText, Bug, ArrowUpDown, ArrowDown, ArrowUp, Server, Cloud, Globe, Upload, Ban } from 'lucide-react';
import { toast } from 'react-toastify';
import ConfigFlowModal from '../../components/integrations/ConfigFlowModal';
import IntegrationDocsModal from '../../components/integrations/IntegrationDocsModal';
import ActionResultModal from '../../components/integrations/ActionResultModal';
import { DebugConsole } from '../../components/integrations/DebugConsole';
import { ExaminationCard } from '../../components/examinations/ExaminationCard';
import type { ActionResult } from '../../services/integrationService';

const IntegrationDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { currentPatient } = usePatientStore();
  const [details, setDetails] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [showDocs, setShowDocs] = useState(false);
  const [isEditingConfig, setIsEditingConfig] = useState(false);
  const [actionResult, setActionResult] = useState<{ result: ActionResult; label: string } | null>(null);
  
  const tabParam = searchParams.get('tab') as 'overview' | 'examinations' | 'biomarkers' | 'data' | 'settings' | null;
  const activeTab = tabParam || 'overview';

  const handleTabChange = (tabId: string) => {
    setSearchParams({ tab: tabId });
  };

  type SortColumn = 'date' | 'sync_time' | 'metric' | 'value';
  type SortDirection = 'asc' | 'desc';
  const [sortColumn, setSortColumn] = useState<SortColumn>('date');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

  const [available, setAvailable] = useState<any[]>([]);

  useEffect(() => {
    if (id && currentPatient) {
      loadDetails();
      integrationService.getAvailable().then(setAvailable).catch(console.error);
    }
  }, [id, currentPatient]);

  const loadDetails = async () => {
    if (!currentPatient || !id) return;
    setLoading(true);
    try {
      const data = await integrationService.getDetails(id, currentPatient.id);
      setDetails(data);
    } catch (error) {
      console.error("Failed to load integration details", error);
      toast.error("Failed to load details");
      navigate('/settings/integrations');
    } finally {
      setLoading(false);
    }
  };

  const handleAuthorize = async () => {
    if (!currentPatient || !id || !details?.domain) return;
    try {
      const { authorize_url } = await integrationService.oauthStart(details.domain, id, currentPatient.id);
      window.location.href = authorize_url;
    } catch (error: any) {
      const detail = error?.response?.data?.detail || 'Authorization failed to start.';
      toast.error(detail);
    }
  };

  const handleSort = (column: SortColumn) => {
    if (sortColumn === column) {
      setSortDirection(prev => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortColumn(column);
      setSortDirection('desc');
    }
  };

  const sortedData = useMemo(() => {
    if (!details?.recent_data) return [];
    return [...details.recent_data].sort((a, b) => {
      let aVal = a[sortColumn];
      let bVal = b[sortColumn];

      if (sortColumn === 'date' || sortColumn === 'sync_time') {
        if (!aVal) return sortDirection === 'asc' ? 1 : -1;
        if (!bVal) return sortDirection === 'asc' ? -1 : 1;
        return sortDirection === 'asc'
          ? new Date(aVal).getTime() - new Date(bVal).getTime()
          : new Date(bVal).getTime() - new Date(aVal).getTime();
      }

      if (sortColumn === 'value') {
        const numA = parseFloat(aVal);
        const numB = parseFloat(bVal);
        if (!isNaN(numA) && !isNaN(numB)) {
          return sortDirection === 'asc' ? numA - numB : numB - numA;
        }
      }

      const strA = String(aVal || '').toLowerCase();
      const strB = String(bVal || '').toLowerCase();
      if (strA < strB) return sortDirection === 'asc' ? -1 : 1;
      if (strA > strB) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });
  }, [details?.recent_data, sortColumn, sortDirection]);

  const handleSync = async () => {
    if (!currentPatient || !id) return;
    setSyncing(true);
    try {
      await integrationService.syncIntegration(id, currentPatient.id);
      toast.success("Sync completed");
      await loadDetails();
    } catch (error) {
      console.error("Sync failed", error);
      toast.error("Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const handleToggleDebug = async () => {
    if (!currentPatient || !id) return;
    try {
      const res = await integrationService.toggleDebugMode(id, currentPatient.id);
      toast.success(res.message);
      await loadDetails();
    } catch (error) {
      console.error("Toggle debug failed", error);
      toast.error("Failed to toggle debug mode");
    }
  };

  const handleRemove = async () => {
    if (!currentPatient || !id) return;
    if (!confirm("Are you sure you want to remove this integration instance? Existing data will not be deleted, but no new data will sync.")) return;
    
    try {
      await integrationService.removeIntegration(id, currentPatient.id);
      toast.success("Integration removed");
      navigate('/settings/integrations');
    } catch (error) {
      console.error("Remove failed", error);
      toast.error("Failed to remove integration");
    }
  };

  const handleCustomAction = async (action: CustomAction) => {
    if (!currentPatient || !id) return;
    try {
      toast.info(`Executing ${action.label}...`);
      const response = await integrationService.executeAction(id, currentPatient.id, action.id);
      toast.success(response.message || "Action completed successfully");
      // If the action returned structured display blocks, show them in the
      // result modal. Otherwise the toast above is enough (backwards compat).
      if (response.results && Array.isArray(response.results) && response.results.length > 0) {
        setActionResult({ result: response, label: action.label });
      }
      await loadDetails();
    } catch (error: any) {
      toast.error(error.response?.data?.detail || "Action failed");
    }
  };

  if (loading) {
    return <LoadingState variant="section" />;
  }

  if (!details) {
    return <div>Not found</div>;
  }

  const isConnected = details.status === 'active' || details.status === 'ACTIVE';
  const isError = details.status === 'error' || details.status === 'ERROR';

  const manifest = available.find(a => a.domain === details.domain);

  const getAccessIcon = (type?: string) => {
    switch(type) {
      case 'local': return <Server className="h-4 w-4 text-gray-500" />;
      case 'cloud': return <Cloud className="h-4 w-4 text-blue-500" />;
      case 'hybrid': return <Globe className="h-4 w-4 text-purple-500" />;
      default: return null;
    }
  };

  const renderSortIcon = (column: SortColumn) => {
    if (sortColumn !== column) return <ArrowUpDown className="w-3 h-3 ml-1 opacity-40 group-hover:opacity-100" />;
    return sortDirection === 'asc' ? <ArrowUp className="w-3 h-3 ml-1 text-blue-500" /> : <ArrowDown className="w-3 h-3 ml-1 text-blue-500" />;
  };

  const renderDirectionBadge = (direction?: string) => {
    if (!direction) return null;
    const map: Record<string, { icon: any; label: string; cls: string }> = {
      both: { icon: ArrowUpDown, label: 'Two-way', cls: 'text-purple-600 bg-purple-50 dark:bg-purple-900/30 dark:text-purple-400' },
      pull_only: { icon: ArrowDown, label: 'Pull only', cls: 'text-blue-600 bg-blue-50 dark:bg-blue-900/30 dark:text-blue-400' },
      push_only: { icon: ArrowUp, label: 'Push only', cls: 'text-emerald-600 bg-emerald-50 dark:bg-emerald-900/30 dark:text-emerald-400' },
      none: { icon: Ban, label: 'Manual only', cls: 'text-gray-500 bg-gray-100 dark:bg-gray-800 dark:text-gray-400' },
    };
    const cfg = map[direction];
    if (!cfg) return null;
    const Icon = cfg.icon;
    return (
      <span className={`flex items-center text-xs font-bold px-2 py-0.5 rounded-full`}>
        <Icon className="w-3 h-3 mr-1" /> {cfg.label}
      </span>
    );
  };

  return (
    <div className="max-w-6xl mx-auto pb-20">
      <div className="mb-6 flex items-center justify-between">
        <button 
          onClick={() => navigate('/settings/integrations')}
          className="flex items-center text-sm font-bold text-gray-500 hover:text-blue-600 transition-colors"
        >
          <ArrowLeft className="w-4 h-4 mr-1" /> Back to Integrations
        </button>
        <div className="flex space-x-3">
          <button 
            onClick={handleToggleDebug}
            className={`flex items-center px-4 py-2 rounded-xl font-bold text-sm transition-colors border ${
              details?.is_debug_enabled 
                ? 'bg-yellow-50 text-yellow-700 border-yellow-200 hover:bg-yellow-100 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800' 
                : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50 dark:bg-dark-surface dark:text-dark-text dark:border-dark-border dark:hover:bg-dark-bg'
            }`}
          >
            <Bug className="w-4 h-4 mr-2" />
            {details?.is_debug_enabled ? 'Debug ON' : 'Debug OFF'}
          </button>
          <button 
            onClick={() => setShowDocs(true)}
            className="flex items-center px-4 py-2 bg-gray-50 text-gray-700 dark:bg-dark-bg dark:text-dark-text rounded-xl font-bold text-sm hover:bg-gray-100 dark:hover:bg-dark-surface transition-colors border border-gray-200 dark:border-dark-border"
          >
            <FileText className="w-4 h-4 mr-2" />
            Documentation
          </button>
          <button 
            onClick={handleSync}
            disabled={syncing || (!isConnected && !isError)}
            className="flex items-center px-4 py-2 bg-blue-50 text-blue-600 rounded-xl font-bold text-sm hover:bg-blue-100 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${syncing ? 'animate-spin' : ''}`} />
            {isError ? 'Retry Sync' : 'Sync Now'}
          </button>
        </div>
      </div>

      <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm mb-6">
        <div className="flex items-start justify-between">
          <div className="flex items-center space-x-4">
            <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-2xl flex items-center justify-center border border-gray-100 dark:border-dark-border">
              <Layers className="w-8 h-8 text-blue-600" />
            </div>
            <div>
              <div className="flex items-center gap-3 mb-2">
                <h1 className="text-2xl font-black text-gray-900 dark:text-dark-text capitalize">
                  {details.instance_name || manifest?.name || details.domain.replace('_', ' ')}
                </h1>
                {details.instance_name && (
                   <span className="px-3 py-1 mt-1 text-xs uppercase font-bold tracking-wider text-gray-500 bg-gray-100 dark:bg-gray-800 dark:text-gray-400 rounded-full whitespace-nowrap">
                     via {manifest?.name || details.domain}
                   </span>
                )}
                {manifest?.access_type && <div className="mt-1">{getAccessIcon(manifest.access_type)}</div>}
              </div>
              
              {manifest?.description && (
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-3 max-w-3xl">
                  {manifest.description}
                </p>
              )}

              <div className="flex items-center flex-wrap gap-3 mt-3">
                {manifest?.author === 'Core' ? (
                  <span className="inline-block px-2 py-0.5 text-[10px] font-semibold tracking-wide text-white bg-blue-600 rounded">
                    OFFICIAL
                  </span>
                ) : manifest?.author ? (
                  <span className="inline-block px-2 py-0.5 text-[10px] font-semibold tracking-wide text-gray-500 bg-gray-100 dark:bg-gray-800 dark:text-gray-400 rounded">
                    COMMUNITY
                  </span>
                ) : null}

                {manifest?.categories?.map((cat: string) => (
                  <span key={cat} className="inline-block px-2 py-0.5 text-[10px] font-semibold tracking-wide text-indigo-600 bg-indigo-50 dark:bg-indigo-900/30 dark:text-indigo-400 rounded border border-indigo-100 dark:border-indigo-800">
                    {cat}
                  </span>
                ))}
                
                {isConnected ? (
                  <span className="flex items-center text-xs font-bold text-emerald-600 bg-emerald-50 dark:bg-emerald-900/30 px-2 py-0.5 rounded-full">
                    <CheckCircle className="w-3 h-3 mr-1" /> Connected
                  </span>
                ) : isError ? (
                  <span className="flex items-center text-xs font-bold text-red-600 bg-red-50 dark:bg-red-900/30 px-2 py-0.5 rounded-full">
                    <XCircle className="w-3 h-3 mr-1" /> Error Requires Attention
                  </span>
                ) : (
                  <span className="flex items-center text-xs font-bold text-gray-500 bg-gray-100 dark:bg-gray-800 px-2 py-0.5 rounded-full">
                    <Settings className="w-3 h-3 mr-1" /> Pending Setup
                  </span>
                )}
                {renderDirectionBadge(details.sync_direction)}
                {details.last_synced_at && (
                  <span className="text-xs text-gray-400 font-medium flex items-center ml-1">
                    <Clock className="w-3 h-3 mr-1" /> Last synced: {new Date(details.last_synced_at).toLocaleString()}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex space-x-1 bg-gray-50 dark:bg-dark-bg p-1.5 rounded-2xl border border-gray-100 dark:border-dark-border mb-8 overflow-x-auto scrollbar-hide">
        {[
          { id: 'overview', label: 'Overview' },
          { id: 'examinations', label: 'Examinations' },
          { id: 'biomarkers', label: 'Biomarkers' },
          { id: 'data', label: 'Raw Data' },
          { id: 'settings', label: 'Settings' }
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => handleTabChange(tab.id)}
            className={`px-6 py-2 text-sm font-bold rounded-xl whitespace-nowrap transition-all ${
              activeTab === tab.id 
                ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' 
                : 'text-gray-500 hover:text-gray-700 dark:text-dark-muted dark:hover:text-dark-text'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {details.status === 'PENDING' && (
            <div className="lg:col-span-2 bg-blue-50 dark:bg-blue-900/20 rounded-[2rem] p-8 border border-blue-200 dark:border-blue-800 shadow-sm">
              <div className="flex items-center justify-between flex-wrap gap-4">
                <div className="flex items-center">
                  <Cloud className="w-6 h-6 mr-3 text-blue-600" />
                  <div>
                    <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">Authorization required</h3>
                    <p className="text-sm text-gray-600 dark:text-dark-muted">
                      This integration needs you to sign in to the external service to complete the connection.
                    </p>
                  </div>
                </div>
                <button
                  onClick={handleAuthorize}
                  className="inline-flex items-center px-5 py-2.5 border border-transparent rounded-xl shadow-sm text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 active:scale-95 transition-all"
                >
                  Authorize
                </button>
              </div>
            </div>
          )}
          {details.custom_actions && details.custom_actions.length > 0 && (
            <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
              <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text mb-6">
                <Zap className="w-5 h-5 mr-2 text-yellow-500" /> Actions
              </h3>
              <div className="flex flex-wrap gap-3">
                {details.custom_actions.map((action: CustomAction) => (
                  <button
                    key={action.id}
                    onClick={() => handleCustomAction(action)}
                    className={`inline-flex items-center px-4 py-2 border rounded-xl shadow-sm text-sm font-medium focus:outline-none ${
                      action.style === 'danger' ? 'border-transparent text-red-700 bg-red-100 hover:bg-red-200' :
                      action.style === 'warning' ? 'border-transparent text-yellow-800 bg-yellow-100 hover:bg-yellow-200' :
                      action.style === 'primary' ? 'border-transparent text-white bg-blue-600 hover:bg-blue-700' :
                      'border-gray-300 text-gray-700 bg-white hover:bg-gray-50 dark:bg-dark-bg dark:text-dark-text dark:border-dark-border dark:hover:bg-dark-surface'
                    }`}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {details.push_status && (
            <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
              <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text mb-6">
                <Upload className="w-5 h-5 mr-2 text-emerald-500" /> Last Push
              </h3>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-4">
                {[
                  { label: 'Pushed', value: details.push_status.pushed, color: 'text-emerald-600' },
                  { label: 'Created', value: details.push_status.created, color: 'text-blue-600' },
                  { label: 'Updated', value: details.push_status.updated, color: 'text-indigo-600' },
                  { label: 'Skipped (412)', value: details.push_status.skipped, color: 'text-gray-500' },
                ].map((stat) => (
                  <div key={stat.label} className="bg-gray-50 dark:bg-dark-bg rounded-xl p-4 border border-gray-100 dark:border-dark-border text-center">
                    <div className={`text-2xl font-black ${stat.color}`}>{stat.value ?? 0}</div>
                    <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mt-1">{stat.label}</div>
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between flex-wrap gap-2 text-xs">
                <span className={`inline-flex items-center px-2 py-0.5 rounded-full font-bold ${details.push_status.errors > 0 ? 'text-red-600 bg-red-50 dark:bg-red-900/30' : 'text-emerald-600 bg-emerald-50 dark:bg-emerald-900/30'}`}>
                  {details.push_status.errors > 0 ? `${details.push_status.errors} error(s)` : 'No errors'}
                </span>
                {details.push_status.at && (
                  <span className="text-gray-400 font-medium flex items-center">
                    <Clock className="w-3 h-3 mr-1" /> {new Date(details.push_status.at).toLocaleString()}
                  </span>
                )}
              </div>
            </div>
          )}
          
          <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm h-96 flex flex-col">
            <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text mb-6">
               <Activity className="w-5 h-5 mr-2 text-gray-400" /> Sync History
            </h3>
            <div className="space-y-4 overflow-y-auto pr-2 custom-scrollbar flex-1">
              {details.sync_history && details.sync_history.length > 0 ? (
                details.sync_history.map((log: any) => (
                  <div key={log.id} className="flex flex-col p-4 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-100 dark:border-dark-border">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[10px] font-black text-gray-400 uppercase tracking-widest">
                        {new Date(log.started_at).toLocaleString()}
                      </span>
                      {log.status === 'success' ? (
                        <span className="flex items-center text-[10px] font-bold text-emerald-600">
                          <CheckCircle className="w-3 h-3 mr-1" /> Success
                        </span>
                      ) : (
                        <span className="flex items-center text-[10px] font-bold text-red-600">
                          <XCircle className="w-3 h-3 mr-1" /> Failed
                        </span>
                      )}
                    </div>
                    <div className="flex items-baseline space-x-1">
                      <span className="text-lg font-black text-gray-700 dark:text-dark-text">{log.records_synced}</span>
                      <span className="text-[10px] font-bold text-gray-400 uppercase">records</span>
                    </div>
                    {log.error_message && (
                      <p className="mt-2 text-xs text-red-500 font-medium">{log.error_message}</p>
                    )}
                  </div>
                ))
              ) : (
                <p className="text-sm text-gray-400 italic text-center py-4">No sync history available</p>
              )}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'examinations' && (
        <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
          <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text mb-6">
            <FileText className="w-5 h-5 mr-2 text-indigo-500" /> Synced Laboratory Reports
          </h3>
          {details.synced_examinations && details.synced_examinations.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 max-h-[800px] overflow-y-auto pr-2 custom-scrollbar">
                {details.synced_examinations.map((exam: any) => (
                  <div key={exam.id}>
                    <ExaminationCard 
                      examination={exam} 
                      showOpenButton={false}
                      onClick={() => navigate(`/examinations/${exam.id}`)}
                      className="hover:border-indigo-300 dark:hover:border-indigo-700 transition-colors"
                    />
                  </div>
                ))}
            </div>
          ) : (
             <div className="text-center py-12 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-dashed border-gray-200 dark:border-dark-border">
                <FileText className="w-8 h-8 mx-auto text-gray-300 mb-3" />
                <p className="text-sm font-medium text-gray-500">No examinations or laboratory reports have been synced yet.</p>
             </div>
          )}
        </div>
      )}

      {activeTab === 'biomarkers' && (
        <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
          <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text mb-6">
            <Database className="w-5 h-5 mr-2 text-blue-500" /> Exposed Data Dictionary
          </h3>
          {details.exposed_items && details.exposed_items.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 max-h-[800px] overflow-y-auto pr-2 custom-scrollbar">
              {details.exposed_items.map((item: any) => (
                <Link 
                  to={`/biomarkers/details/${item.id}`} 
                  key={item.id}
                  className="flex flex-col p-4 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-gray-100 dark:border-dark-border hover:border-blue-200 dark:hover:border-blue-900 transition-all group"
                >
                  <span className="text-sm font-bold text-gray-900 dark:text-dark-text group-hover:text-blue-600">{item.name}</span>
                  <div className="flex items-center justify-between mt-2">
                    <div className="flex items-center space-x-2">
                      <span className="text-[10px] uppercase font-black text-gray-400 bg-white dark:bg-dark-surface px-2 py-0.5 rounded border border-gray-100 dark:border-dark-border">{item.category}</span>
                      {details.synced_examinations && details.synced_examinations.length > 0 && (
                         <span className="text-[10px] uppercase font-bold text-indigo-400 bg-indigo-50 dark:bg-indigo-900/30 px-1.5 py-0.5 rounded flex items-center">
                           <FileText className="w-2.5 h-2.5 mr-1" />
                           Report Sourced
                         </span>
                      )}
                    </div>
                    {item.last_seen && (
                      <span className="text-[10px] text-gray-400 font-medium">Seen: {new Date(item.last_seen).toLocaleDateString()}</span>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <div className="text-center py-12 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-dashed border-gray-200 dark:border-dark-border">
              <Database className="w-8 h-8 mx-auto text-gray-300 mb-3" />
              <p className="text-sm font-medium text-gray-500">No biomarker data dictionary has been established yet.</p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'data' && (
        <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
          <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text mb-6">
            <Activity className="w-5 h-5 mr-2 text-green-500" /> Recent Raw Measurements
          </h3>
          {details.recent_data && details.recent_data.length > 0 ? (
            <div className="max-h-[800px] overflow-y-auto custom-scrollbar border rounded-xl border-gray-100 dark:border-dark-border">
              <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
                <thead className="bg-gray-50 dark:bg-dark-bg sticky top-0 z-10">
                  <tr>
                    <th 
                      className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase tracking-wider cursor-pointer group hover:bg-gray-100 dark:hover:bg-dark-surface transition-colors"
                      onClick={() => handleSort('date')}
                    >
                      <div className="flex items-center">Recorded Time {renderSortIcon('date')}</div>
                    </th>
                    <th 
                      className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase tracking-wider cursor-pointer group hover:bg-gray-100 dark:hover:bg-dark-surface transition-colors"
                      onClick={() => handleSort('sync_time')}
                    >
                      <div className="flex items-center">Synced Time {renderSortIcon('sync_time')}</div>
                    </th>
                    <th 
                      className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase tracking-wider cursor-pointer group hover:bg-gray-100 dark:hover:bg-dark-surface transition-colors"
                      onClick={() => handleSort('metric')}
                    >
                      <div className="flex items-center">Metric {renderSortIcon('metric')}</div>
                    </th>
                    <th 
                      className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase tracking-wider cursor-pointer group hover:bg-gray-100 dark:hover:bg-dark-surface transition-colors"
                      onClick={() => handleSort('value')}
                    >
                      <div className="flex items-center">Value {renderSortIcon('value')}</div>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50 dark:divide-dark-border bg-white dark:bg-dark-surface">
                  {sortedData.map((item: any) => (
                    <tr key={item.id} className="hover:bg-gray-50 dark:hover:bg-dark-bg/50 transition-colors">
                      <td className="px-4 py-3 whitespace-nowrap text-xs text-gray-500 dark:text-dark-muted">
                        {new Date(item.date).toLocaleString(undefined, {
                          month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                        })}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-xs text-gray-400 dark:text-dark-muted italic">
                        {item.sync_time ? new Date(item.sync_time).toLocaleString(undefined, {
                          month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                        }) : 'Unknown'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm font-bold text-gray-900 dark:text-dark-text">
                        <div className="flex items-center space-x-2">
                          {item.biomarker_id ? (
                            <Link to={`/biomarkers/details/${item.biomarker_id}`} className="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 hover:underline transition-colors">
                              {item.metric}
                            </Link>
                          ) : (
                            <span>{item.metric}</span>
                          )}
                          {item.examination_id && (
                            <Link 
                              to={`/examinations/${item.examination_id}`}
                              title="View source examination"
                              className="flex items-center justify-center p-1 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 rounded-md hover:bg-indigo-100 dark:hover:bg-indigo-900/50 transition-colors"
                            >
                              <FileText className="w-3 h-3" />
                            </Link>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-900 dark:text-dark-text">
                        <span className="font-black text-blue-600 dark:text-blue-400">{item.value}</span>
                        <span className="ml-1 text-xs text-gray-500 dark:text-dark-muted font-bold">{item.unit}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center py-12 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-dashed border-gray-200 dark:border-dark-border">
              <Activity className="w-8 h-8 mx-auto text-gray-300 mb-3" />
              <p className="text-sm font-medium text-gray-500">No recent raw data found.</p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'settings' && (
        <div className="space-y-8">
          <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
            <div className="flex items-center justify-between mb-6">
              <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text">
                <Settings className="w-5 h-5 mr-2 text-gray-400" /> Configuration
              </h3>
              <button
                onClick={() => setIsEditingConfig(true)}
                className="flex items-center space-x-2 px-3 py-1.5 text-xs font-bold text-gray-600 dark:text-dark-muted hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg transition-colors border border-gray-200 dark:border-dark-border hover:border-blue-200 dark:hover:border-blue-800"
              >
                <Edit2 className="w-3 h-3" />
                <span>Edit Configuration</span>
              </button>
            </div>
            {details.user_config ? (
              <pre className="bg-gray-50 dark:bg-dark-bg p-4 rounded-xl text-xs text-gray-600 dark:text-dark-muted overflow-x-auto border border-gray-100 dark:border-dark-border">
                {JSON.stringify(details.user_config, null, 2)}
              </pre>
            ) : (
              <p className="text-sm text-gray-400">No specific configuration.</p>
            )}
          </div>
          
          {details.is_debug_enabled && (
            <DebugConsole integrationId={id!} patientId={currentPatient!.id} />
          )}

          <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-red-200 dark:border-red-900/30 shadow-sm">
            <h3 className="flex items-center text-lg font-bold text-red-600 dark:text-red-400 mb-6">
              Danger Zone
            </h3>
            <p className="text-sm text-gray-500 dark:text-dark-muted mb-4">
              Removing this integration will stop all future data syncing. Existing data will remain in your records.
            </p>
            <button 
              onClick={handleRemove}
              className="flex items-center px-4 py-2 bg-red-50 text-red-600 rounded-xl font-bold text-sm hover:bg-red-100 transition-colors"
            >
              <Trash2 className="w-4 h-4 mr-2" />
              Remove Integration
            </button>
          </div>
        </div>
      )}

      {isEditingConfig && details?.domain && (
        <ConfigFlowModal
          domain={details.domain}
          integrationId={id}
          initialData={details.user_config || {}}
          onClose={() => setIsEditingConfig(false)}
          onSuccess={() => {
            setIsEditingConfig(false);
            loadDetails();
          }}
        />
      )}

      {showDocs && details?.domain && (
        <IntegrationDocsModal
          domain={details.domain}
          onClose={() => setShowDocs(false)}
        />
      )}

      {actionResult && (
        <ActionResultModal
          result={actionResult.result}
          actionLabel={actionResult.label}
          onClose={() => setActionResult(null)}
        />
      )}
    </div>
  );
};

export default IntegrationDetail;
