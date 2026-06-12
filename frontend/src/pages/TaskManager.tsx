import { useState, useEffect } from 'react';
import { Activity, AlertCircle, Clock, RefreshCw, FileText, Search, Filter, Terminal, Cpu } from 'lucide-react';
import api from '../api/axios';
import { TaskLogViewer } from '../components/ui/TaskLogViewer';
import { PageHeader } from '../components/ui/PageHeader';
import { StickyToolbar } from '../components/ui/StickyToolbar';

interface ProcessingDocument {
  id: string;
  examination_id?: string;
  filename: string;
  status: string;
  progress: number;
  created_at: string;
  age_minutes: number;
  error_message?: string;
}

interface ProcessingExamination {
  id: string;
  category: string;
  status: string;
  progress: number;
  created_at: string;
  age_minutes: number;
  error_message?: string;
}

interface TaskStats {
  documents: {
    by_status: Record<string, number>;
    stalled: number;
  };
  examinations: {
    by_status: Record<string, number>;
    stalled: number;
  };
}

function TaskManager() {
  const [processingDocs, setProcessingDocs] = useState<ProcessingDocument[]>([]);
  const [processingExams, setProcessingExams] = useState<ProcessingExamination[]>([]);
  const [stats, setStats] = useState<TaskStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [isLogViewerOpen, setIsLogViewerOpen] = useState(false);
  const [selectedLogId, setSelectedLogId] = useState<string | null>(null);

  const openLogs = (id: string) => {
    setSelectedLogId(id);
    setIsLogViewerOpen(true);
  };

  const fetchTaskData = async () => {
    setLoading(true);
    try {
      const [docsRes, examsRes, statsRes] = await Promise.all([
        api.get('/task-monitor/documents/processing'),
        api.get('/task-monitor/examinations/processing'),
        api.get('/task-monitor/stats')
      ]);
      setProcessingDocs(docsRes.data);
      setProcessingExams(examsRes.data);
      setStats(statsRes.data);
    } catch (error) {
      console.error('Failed to fetch task data', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTaskData();
  }, []);

  const retryDocument = async (docId: string) => {
    try {
      await api.post(`/task-monitor/documents/retry/${docId}`);
      await fetchTaskData();
    } catch (error) {
      console.error('Failed to retry document', error);
      alert('Failed to retry document');
    }
  };

  const retryExamination = async (examId: string) => {
    try {
      await api.post(`/task-monitor/examinations/retry/${examId}`);
      await fetchTaskData();
    } catch (error) {
      console.error('Failed to retry examination', error);
      alert('Failed to retry examination');
    }
  };

  const filteredDocs = processingDocs.filter(doc => {
    const matchesStatus = filterStatus === 'all' || doc.status === filterStatus;
    const matchesSearch = doc.filename.toLowerCase().includes(searchTerm.toLowerCase());
    return matchesStatus && matchesSearch;
  });

  const filteredExams = processingExams.filter(exam => {
    const matchesStatus = filterStatus === 'all' || exam.status === filterStatus;
    const matchesSearch = exam.category?.toLowerCase().includes(searchTerm.toLowerCase());
    return matchesStatus && matchesSearch;
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center py-40">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto pb-10">
      <PageHeader
        title="Task Monitor"
        subtitle="Debug and monitor background processing tasks"
        icon={<Cpu className="w-8 h-8" />}
        breadcrumbs={[]}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <button 
            onClick={fetchTaskData}
            className="flex items-center space-x-2 px-6 py-2.5 bg-blue-600 text-white rounded-xl hover:bg-blue-700 transition-all shadow-lg shadow-blue-200/50 dark:shadow-none font-bold active:scale-95"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        }
      />

      {/* Statistics Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="bg-white dark:bg-dark-surface rounded-xl p-6 border border-gray-100 dark:border-dark-border">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-3">
              <FileText className="w-5 h-5 text-blue-600" />
              <h3 className="text-sm font-bold uppercase">Documents</h3>
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex justify-between">
              <span className="text-xs text-gray-500">Processing:</span>
              <span className="text-sm font-bold">{stats?.documents.by_status['processing'] || 0}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-xs text-gray-500">Stalled:</span>
              <span className="text-sm font-bold text-red-600">{stats?.documents.stalled || 0}</span>
            </div>
          </div>
        </div>

        <div className="bg-white dark:bg-dark-surface rounded-xl p-6 border border-gray-100 dark:border-dark-border">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-3">
              <Activity className="w-5 h-5 text-indigo-600" />
              <h3 className="text-sm font-bold uppercase">Examinations</h3>
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex justify-between">
              <span className="text-xs text-gray-500">Processing:</span>
              <span className="text-sm font-bold">{stats?.examinations.by_status['processing'] || 0}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-xs text-gray-500">Stalled:</span>
              <span className="text-sm font-bold text-red-600">{stats?.examinations.stalled || 0}</span>
            </div>
          </div>
        </div>

        <div className="bg-white dark:bg-dark-surface rounded-xl p-6 border border-gray-100 dark:border-dark-border">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-3">
              <Clock className="w-5 h-5 text-amber-600" />
              <h3 className="text-sm font-bold uppercase">System Health</h3>
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex justify-between">
              <span className="text-xs text-gray-500">Total Stalled:</span>
              <span className="text-sm font-bold text-red-600">
                {(stats?.documents.stalled || 0) + (stats?.examinations.stalled || 0)}
              </span>
            </div>
            <div className="text-xs text-gray-400 mt-2">
              Tasks running {'>'} 10 minutes
            </div>
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center space-x-4 mb-6 bg-gray-50 dark:bg-dark-bg p-4 rounded-xl">
        <div className="flex items-center space-x-2">
          <Filter className="w-4 h-4 text-gray-400" />
          <select 
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="px-3 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm"
          >
            <option value="all">All Status</option>
            <option value="processing">Processing</option>
            <option value="uploaded">Uploaded</option>
            <option value="failed">Failed</option>
          </select>
        </div>
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input 
            type="text"
            placeholder="Search..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-sm"
          />
        </div>
      </div>

      {/* Documents Table */}
      <div className="bg-white dark:bg-dark-surface rounded-xl border border-gray-100 dark:border-dark-border overflow-hidden mb-8">
        <div className="p-4 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
          <h2 className="text-lg font-bold">Processing Documents ({filteredDocs.length})</h2>
          {stats && stats.documents.stalled > 0 && (
            <div className="flex items-center space-x-2 text-red-600">
              <AlertCircle className="w-4 h-4" />
              <span className="text-sm font-bold">{stats.documents.stalled} stalled</span>
            </div>
          )}
          <div className="bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-900/30 rounded-lg p-4 mt-4">
            <div className="flex items-start space-x-3">
              <AlertCircle className="w-5 h-5 text-amber-600" />
              <div className="text-xs text-amber-800 dark:text-amber-200">
                <p className="font-bold mb-2">Documents stuck without error messages:</p>
                <ul className="list-disc list-inside space-y-1">
                  <li>OCR API call hanging (check API provider status)</li>
                  <li>File path inaccessible or permissions issue</li>
                  <li>Celery worker crashed during processing</li>
                  <li>Network timeout or connection error</li>
                </ul>
                <p className="mt-2 font-bold">Action: Click "Retry OCR" to restart processing. If it fails again, check Celery logs.</p>
              </div>
            </div>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead className="bg-gray-50 dark:bg-dark-bg">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-bold uppercase">Filename</th>
                <th className="px-6 py-3 text-left text-xs font-bold uppercase">Status</th>
                <th className="px-6 py-3 text-left text-xs font-bold uppercase">Progress</th>
                <th className="px-6 py-3 text-left text-xs font-bold uppercase">Age</th>
                <th className="px-6 py-3 text-left text-xs font-bold uppercase">Error</th>
                <th className="px-6 py-3 text-right text-xs font-bold uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-dark-border">
              {filteredDocs.map(doc => (
                <tr key={doc.id} className="hover:bg-gray-50 dark:hover:bg-dark-bg">
                  <td className="px-6 py-4 text-sm font-bold">{doc.filename}</td>
                  <td className="px-6 py-4">
                    <span className={`px-2 py-1 rounded text-xs font-bold uppercase ${
                      doc.status === 'completed' ? 'bg-green-100 text-green-700' :
                      doc.status === 'failed' ? 'bg-red-100 text-red-700' :
                      'bg-yellow-100 text-yellow-700'
                    }`}>
                      {doc.status}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center space-x-2">
                      <div className="w-20 h-2 bg-gray-200 rounded-full overflow-hidden">
                        <div className="h-full bg-blue-600" style={{ width: `${doc.progress}%` }} />
                      </div>
                      <span className="text-xs">{doc.progress}%</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-xs">
                    <div className="flex items-center space-x-1">
                      {doc.age_minutes > 60 && (
                        <span className="px-2 py-0.5 bg-red-100 text-red-700 rounded text-[9px] font-bold uppercase">Stalled</span>
                      )}
                      {doc.age_minutes > 10 && doc.age_minutes <= 60 && (
                        <Clock className="w-3 h-3 text-amber-600" />
                      )}
                      <span>{Math.round(doc.age_minutes)} min</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-xs max-w-48">
                    {doc.error_message ? (
                      <span className="text-red-600 truncate">{doc.error_message}</span>
                    ) : (
                      <span className="text-amber-600">
                        {doc.age_minutes > 60 ? 'Stalled (>1 hour)' : 'Processing'}
                      </span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end space-x-2">
                      <button 
                        onClick={() => openLogs(doc.examination_id || doc.id)}
                        className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded text-blue-600 transition-colors"
                        title="View technical logs"
                      >
                        <Terminal className="w-4 h-4" />
                      </button>
                      <button 
                        onClick={() => retryDocument(doc.id)}
                        className="px-3 py-1 bg-blue-600 text-white rounded text-xs hover:bg-blue-700 transition-colors"
                        title="Retry OCR processing"
                      >
                        Retry OCR
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {filteredDocs.length === 0 && (
                <tr>
<td className="px-6 py-8 text-center text-gray-400" colSpan={6}>
                  No processing documents found
                </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Examinations Table */}
      <div className="bg-white dark:bg-dark-surface rounded-xl border border-gray-100 dark:border-dark-border overflow-hidden">
        <div className="p-4 border-b border-gray-100 dark:border-dark-border flex items-center justify-between">
          <h2 className="text-lg font-bold">Processing Examinations ({filteredExams.length})</h2>
          {stats && stats.examinations.stalled > 0 && (
            <div className="flex items-center space-x-2 text-red-600">
              <AlertCircle className="w-4 h-4" />
              <span className="text-sm font-bold">{stats.examinations.stalled} stalled</span>
            </div>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead className="bg-gray-50 dark:bg-dark-bg">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-bold uppercase">Category</th>
                <th className="px-6 py-3 text-left text-xs font-bold uppercase">Status</th>
                <th className="px-6 py-3 text-left text-xs font-bold uppercase">Progress</th>
                <th className="px-6 py-3 text-left text-xs font-bold uppercase">Age</th>
                <th className="px-6 py-3 text-left text-xs font-bold uppercase">Error</th>
                <th className="px-6 py-3 text-right text-xs font-bold uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-dark-border">
              {filteredExams.map(exam => (
                <tr key={exam.id} className="hover:bg-gray-50 dark:hover:bg-dark-bg">
                  <td className="px-6 py-4 text-sm font-bold">{exam.category || 'General'}</td>
                  <td className="px-6 py-4">
                    <span className={`px-2 py-1 rounded text-xs font-bold uppercase ${
                      exam.status === 'completed' ? 'bg-green-100 text-green-700' :
                      exam.status === 'failed' ? 'bg-red-100 text-red-700' :
                      'bg-blue-100 text-blue-700'
                    }`}>
                      {exam.status}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center space-x-2">
                      <div className="w-20 h-2 bg-gray-200 rounded-full overflow-hidden">
                        <div className="h-full bg-indigo-600" style={{ width: `${exam.progress}%` }} />
                      </div>
                      <span className="text-xs">{exam.progress}%</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-xs">
                    <div className="flex items-center space-x-1">
                      {exam.age_minutes > 10 && <Clock className="w-3 h-3 text-amber-600" />}
                      <span>{Math.round(exam.age_minutes)} min</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-xs max-w-48">
                    {exam.error_message ? (
                      <span className="text-red-600 truncate" title={exam.error_message}>{exam.error_message}</span>
                    ) : (
                      <span className="text-indigo-600">
                        {exam.status === 'aggregating' ? 'Aggregating Text' : 
                         exam.status === 'analyzing_text' ? 'AI Analysis (Pass 1)' :
                         exam.status === 'defining_ontology' ? 'AI Ontology (Pass 2)' :
                         exam.status === 'persisting_results' ? 'Saving to DB' : 'Processing'}
                      </span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end space-x-2">
                      <button 
                        onClick={() => openLogs(exam.id)}
                        className="p-2 hover:bg-gray-100 dark:hover:bg-dark-bg rounded text-indigo-600 transition-colors"
                        title="View technical logs"
                      >
                        <Terminal className="w-4 h-4" />
                      </button>
                      {exam.status !== 'completed' && (
                        <button 
                          onClick={() => retryExamination(exam.id)}
                          className="px-3 py-1 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700"
                        >
                          Retry
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {filteredExams.length === 0 && (
                <tr>
<td className="px-6 py-8 text-center text-gray-400" colSpan={5}>
                  No processing examinations found
                </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
      
      {selectedLogId && (
        <TaskLogViewer 
          examinationId={selectedLogId}
          isOpen={isLogViewerOpen}
          onClose={() => {
            setIsLogViewerOpen(false);
            setSelectedLogId(null);
          }}
        />
      )}
    </div>
  );
}

export default TaskManager;