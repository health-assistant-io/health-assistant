import React, { useState, useEffect, useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { integrationService, CustomAction } from '../../services/integrationService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { PageHeader } from '../../components/ui/PageHeader';
import { LoadingState } from '../../components/ui/LoadingState';
import { Activity, ArrowLeft, RefreshCw, Layers, Database, Clock, Settings, Trash2, CheckCircle, XCircle, Edit2, Zap, FileText, Bug, ArrowUpDown, ArrowDown, ArrowUp } from 'lucide-react';
import { toast } from 'react-toastify';
import ConfigFlowModal from '../../components/integrations/ConfigFlowModal';
import IntegrationDocsModal from '../../components/integrations/IntegrationDocsModal';
import { DebugConsole } from '../../components/integrations/DebugConsole';
import { ExaminationCard } from '../../components/examinations/ExaminationCard';

const IntegrationDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { currentPatient } = usePatientStore();
  const [details, setDetails] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [showDocs, setShowDocs] = useState(false);
  const [isEditingConfig, setIsEditingConfig] = useState(false);

  type SortColumn = 'date' | 'sync_time' | 'metric' | 'value';
  type SortDirection = 'asc' | 'desc';
  const [sortColumn, setSortColumn] = useState<SortColumn>('date');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

  useEffect(() => {
    if (id && currentPatient) {
      loadDetails();
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

  const renderSortIcon = (column: SortColumn) => {
    if (sortColumn !== column) return <ArrowUpDown className="w-3 h-3 ml-1 opacity-40 group-hover:opacity-100" />;
    return sortDirection === 'asc' ? <ArrowUp className="w-3 h-3 ml-1 text-blue-500" /> : <ArrowDown className="w-3 h-3 ml-1 text-blue-500" />;
  };

  return (
    <div className="max-w-5xl mx-auto pb-20">
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
          <button 
            onClick={handleRemove}
            className="flex items-center px-4 py-2 bg-red-50 text-red-600 rounded-xl font-bold text-sm hover:bg-red-100 transition-colors"
          >
            <Trash2 className="w-4 h-4 mr-2" />
            Remove
          </button>
        </div>
      </div>

      <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm mb-8">
        <div className="flex items-start justify-between">
          <div className="flex items-center space-x-4">
            <div className="w-16 h-16 bg-gray-50 dark:bg-dark-bg rounded-2xl flex items-center justify-center border border-gray-100 dark:border-dark-border">
              <Layers className="w-8 h-8 text-blue-600" />
            </div>
            <div>
              <h1 className="text-2xl font-black text-gray-900 dark:text-dark-text capitalize">
                {details.instance_name || details.domain.replace('_', ' ')}
              </h1>
              <div className="flex items-center mt-1">
                {isConnected ? (
                  <span className="flex items-center text-xs font-bold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">
                    <CheckCircle className="w-3 h-3 mr-1" /> Connected
                  </span>
                ) : isError ? (
                  <span className="flex items-center text-xs font-bold text-red-600 bg-red-50 px-2 py-0.5 rounded-full">
                    <XCircle className="w-3 h-3 mr-1" /> Error Requires Attention
                  </span>
                ) : (
                  <span className="flex items-center text-xs font-bold text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full">
                    <Settings className="w-3 h-3 mr-1" /> Pending Setup
                  </span>
                )}
                {details.last_synced_at && (
                  <span className="ml-3 text-xs text-gray-400 font-medium flex items-center">
                    <Clock className="w-3 h-3 mr-1" /> Last synced: {new Date(details.last_synced_at).toLocaleString()}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Col: Config & Items */}
        <div className="lg:col-span-2 space-y-8">
          
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

          {details.synced_examinations && details.synced_examinations.length > 0 && (
            <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
              <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text mb-6">
                <FileText className="w-5 h-5 mr-2 text-indigo-500" /> Synced Laboratory Reports
              </h3>
              <div className="flex overflow-x-auto space-x-4 pb-4 scrollbar-hide">
                {details.synced_examinations.map((exam: any) => (
                  <div key={exam.id} className="flex-shrink-0 w-80">
                    <ExaminationCard examination={exam} />
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
            <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text mb-6">
              <Database className="w-5 h-5 mr-2 text-blue-500" /> Exposed Data Dictionary
            </h3>
            {details.exposed_items && details.exposed_items.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {details.exposed_items.map((item: any) => (
                  <Link 
                    to={`/biomarkers/details/${item.slug}`} 
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
              <div className="text-center py-8 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-dashed border-gray-200 dark:border-dark-border">
                <p className="text-sm font-medium text-gray-400">No data has been synced yet.</p>
              </div>
            )}
          </div>

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

          <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
            <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text mb-6">
              <Activity className="w-5 h-5 mr-2 text-green-500" /> Recent Measurements
            </h3>
            {details.recent_data && details.recent_data.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-100 dark:divide-dark-border">
                  <thead>
                    <tr>
                      <th 
                        className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase tracking-wider cursor-pointer group hover:bg-gray-50 dark:hover:bg-dark-bg/50 transition-colors"
                        onClick={() => handleSort('date')}
                      >
                        <div className="flex items-center">Recorded Time {renderSortIcon('date')}</div>
                      </th>
                      <th 
                        className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase tracking-wider cursor-pointer group hover:bg-gray-50 dark:hover:bg-dark-bg/50 transition-colors"
                        onClick={() => handleSort('sync_time')}
                      >
                        <div className="flex items-center">Synced Time {renderSortIcon('sync_time')}</div>
                      </th>
                      <th 
                        className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase tracking-wider cursor-pointer group hover:bg-gray-50 dark:hover:bg-dark-bg/50 transition-colors"
                        onClick={() => handleSort('metric')}
                      >
                        <div className="flex items-center">Metric {renderSortIcon('metric')}</div>
                      </th>
                      <th 
                        className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-dark-muted uppercase tracking-wider cursor-pointer group hover:bg-gray-50 dark:hover:bg-dark-bg/50 transition-colors"
                        onClick={() => handleSort('value')}
                      >
                        <div className="flex items-center">Value {renderSortIcon('value')}</div>
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
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
                            {item.slug ? (
                              <Link to={`/biomarkers/details/${item.slug}`} className="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 hover:underline transition-colors">
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
              <div className="text-center py-8 bg-gray-50 dark:bg-dark-bg rounded-2xl border border-dashed border-gray-200 dark:border-dark-border">
                <p className="text-sm font-medium text-gray-400">No recent data found.</p>
              </div>
            )}
          </div>
        </div>

        {/* Right Col: Sync History */}
        <div className="space-y-8">
          <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-6 border border-gray-100 dark:border-dark-border shadow-sm">
            <h3 className="text-sm font-bold text-gray-900 dark:text-dark-text mb-6 uppercase tracking-widest">Sync History</h3>
            <div className="space-y-4">
              {details.sync_history && details.sync_history.length > 0 ? (
                details.sync_history.map((log: any) => (
                  <div key={log.id} className="flex flex-col p-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-100 dark:border-dark-border">
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
                <p className="text-xs text-gray-400 italic text-center py-4">No sync history available</p>
              )}
            </div>
          </div>
        </div>
      </div>

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
    </div>
  );
};

export default IntegrationDetail;
