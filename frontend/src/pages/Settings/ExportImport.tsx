import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';
import {
  Database,
  Download,
  Upload,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  FileArchive,
  Loader2,
  Info,
  X,
} from 'lucide-react';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { useAuthStore } from '../../store/slices/authSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { usePatientStore } from '../../store/slices/patientSlice';
import { Modal } from '../../components/ui/Modal';
import { AIBadge } from '../../components/ui/AIBadge';
import {
  createExportJob,
  listExportJobs,
  getExportJob,
  downloadExportFile,
  importBackupFile,
  getImportJob,
} from '../../services/backupService';
import { listPatients } from '../../services/fhirService';
import type {
  BackupRequest,
  ExportJob,
  ExportScope,
  ExportType,
  ImportJob,
  JobStatus,
} from '../../types/backup';
import {
  EXPORT_SCOPE_LABELS,
  EXPORT_TYPE_LABELS,
  JOB_STATUS_COLORS,
  TERMINAL_STATUSES,
} from '../../types/backup';
import type { Patient } from '../../types/fhir';

const POLL_INTERVAL_MS = 3000;
const STALL_TIMEOUT_MS = 5 * 60 * 1000;

type PageTab = 'export' | 'import';
type ImportMessage = { type: 'success' | 'error' | 'info'; text: string } | null;

const ExportImport: React.FC = () => {
  const { t } = useTranslation();
  const { user } = useAuthStore();
  const showConfirmation = useUIStore((s) => s.showConfirmation);
  const { currentPatient } = usePatientStore();

  const [activeTab, setActiveTab] = useState<PageTab>('export');
  const [isRefreshing, setIsRefreshing] = useState(false);

  const [exportJobs, setExportJobs] = useState<ExportJob[]>([]);
  const [importJobs, setImportJobs] = useState<ImportJob[]>([]);

  const [scope, setScope] = useState<ExportScope>('patient');
  const [exportType, setExportType] = useState<ExportType>('fhir_only');
  const [patientIds, setPatientIds] = useState<string[]>([]);
  const [availablePatients, setAvailablePatients] = useState<Patient[]>([]);
  const [includeDocuments, setIncludeDocuments] = useState(true);
  const [includeTelemetry, setIncludeTelemetry] = useState(true);
  const [includeIntegrations, setIncludeIntegrations] = useState(true);
  const [includeAiConfig, setIncludeAiConfig] = useState(false);
  const [isSubmittingExport, setIsSubmittingExport] = useState(false);

  const [importMessage, setImportMessage] = useState<ImportMessage>(null);
  const [isImporting, setIsImporting] = useState(false);

  const [selectedExportJob, setSelectedExportJob] = useState<ExportJob | null>(null);
  const [selectedImportJob, setSelectedImportJob] = useState<ImportJob | null>(null);

  const [autoMapBiomarkers, setAutoMapBiomarkers] = useState(true);
  const [useAiNormalization, setUseAiNormalization] = useState(false);

  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const role = user?.role;
  const canUseGroupScope = role === 'MANAGER' || role === 'ADMIN' || role === 'SYSTEM_ADMIN';
  const canUseSystemScope = role === 'ADMIN' || role === 'SYSTEM_ADMIN';
  const canUseAiConfig = role === 'SYSTEM_ADMIN';

  const fetchExportJobs = useCallback(async (showSpinner = true) => {
    if (showSpinner) setIsRefreshing(true);
    try {
      const res = await listExportJobs(50);
      setExportJobs(res.items || []);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to load export jobs');
    } finally {
      if (showSpinner) setIsRefreshing(false);
    }
  }, []);

  const pollExportJob = useCallback((jobId: string) => {
    if (pollTimers.current[jobId]) return;
    const startedAt = Date.now();
    pollTimers.current[jobId] = setInterval(async () => {
      try {
        const job = await getExportJob(jobId);
        setExportJobs((prev) => prev.map((j) => (j.id === jobId ? job : j)));
        if (TERMINAL_STATUSES.includes(job.status)) {
          if (pollTimers.current[jobId]) {
            clearInterval(pollTimers.current[jobId]);
            delete pollTimers.current[jobId];
          }
          if (job.status === 'COMPLETED') {
            toast.success(`Export ${job.export_type} ready`);
          } else if (job.status === 'FAILED') {
            toast.error(job.error_message || 'Export failed');
          } else if (job.status === 'PARTIAL') {
            toast.warn('Export completed with warnings');
          }
        }
        if (Date.now() - startedAt > STALL_TIMEOUT_MS) {
          if (pollTimers.current[jobId]) {
            clearInterval(pollTimers.current[jobId]);
            delete pollTimers.current[jobId];
          }
          toast.warn('Export job polling timed out — refresh to check status');
        }
      } catch (err) {
        console.error('Polling export job failed', err);
      }
    }, POLL_INTERVAL_MS);
  }, []);

  const pollImportJob = useCallback((jobId: string) => {
    const key = `import-${jobId}`;
    if (pollTimers.current[key]) return;
    const startedAt = Date.now();
    pollTimers.current[key] = setInterval(async () => {
      try {
        const job = await getImportJob(jobId);
        setImportJobs((prev) => {
          const exists = prev.some((j) => j.id === jobId);
          if (exists) return prev.map((j) => (j.id === jobId ? job : j));
          return [job, ...prev];
        });
        if (TERMINAL_STATUSES.includes(job.status)) {
          if (pollTimers.current[key]) {
            clearInterval(pollTimers.current[key]);
            delete pollTimers.current[key];
          }
          if (job.status === 'COMPLETED') {
            toast.success(
              `Import complete — ${job.processed_records ?? 0} record(s) restored`
            );
          } else if (job.status === 'FAILED') {
            toast.error(job.error_message || 'Import failed');
          } else if (job.status === 'PARTIAL') {
            toast.warn(
              `Import partial — ${job.failed_records ?? 0} failure(s)`
            );
          }
        }
        if (Date.now() - startedAt > STALL_TIMEOUT_MS) {
          if (pollTimers.current[key]) {
            clearInterval(pollTimers.current[key]);
            delete pollTimers.current[key];
          }
          toast.warn('Import job polling timed out — refresh to check status');
        }
      } catch (err) {
        console.error('Polling import job failed', err);
      }
    }, POLL_INTERVAL_MS);
  }, []);

  useEffect(() => {
    fetchExportJobs(false);
    return () => {
      Object.values(pollTimers.current).forEach((id) => clearInterval(id));
      pollTimers.current = {};
    };
  }, [fetchExportJobs]);

  useEffect(() => {
    if (scope !== 'patient' && scope !== 'group') return;
    if (!user?.tenant_id) return;
    listPatients(user.tenant_id, 200)
      .then((res) => setAvailablePatients(res.items || []))
      .catch((err) => {
        console.error('Failed to load patients for export selection', err);
      });
  }, [scope, user?.tenant_id]);

  useEffect(() => {
    if (scope === 'patient' && currentPatient?.id) {
      setPatientIds([currentPatient.id]);
    } else if (scope === 'patient' && !currentPatient?.id) {
      setPatientIds([]);
    }
  }, [scope, currentPatient?.id]);

  const handleScopeChange = (next: ExportScope) => {
    if (next === 'group' && !canUseGroupScope) {
      toast.error('Group scope requires MANAGER, ADMIN or SYSTEM_ADMIN role');
      return;
    }
    if (next === 'system' && !canUseSystemScope) {
      toast.error('System scope requires ADMIN or SYSTEM_ADMIN role');
      return;
    }
    setScope(next);
    if (next === 'patient' && currentPatient?.id) {
      setPatientIds([currentPatient.id]);
    } else if (next === 'system') {
      setPatientIds([]);
    }
  };

  const handlePatientToggle = (id: string) => {
    if (scope === 'patient') {
      setPatientIds([id]);
    } else {
      setPatientIds((prev) =>
        prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
      );
    }
  };

  const handleExportSubmit = async () => {
    if (scope === 'patient' && patientIds.length === 0) {
      toast.error('Select at least one patient for patient-scoped export');
      return;
    }
    if (scope === 'patient' && role === 'USER' && patientIds.length > 1) {
      toast.error('USER role can only export a single patient');
      return;
    }
    if (scope === 'group' && patientIds.length === 0) {
      toast.error('Select at least one patient for group-scoped export');
      return;
    }
    const request: BackupRequest = {
      scope,
      export_type: exportType,
      patient_ids: scope === 'system' ? undefined : patientIds,
      include_documents: includeDocuments,
      include_telemetry: includeTelemetry,
      include_integrations: includeIntegrations,
      include_ai_config: canUseAiConfig ? includeAiConfig : false,
    };
    setIsSubmittingExport(true);
    try {
      const job = await createExportJob(request);
      setExportJobs((prev) => [job, ...prev]);
      pollExportJob(job.id);
      toast.success('Export job started');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to start export');
    } finally {
      setIsSubmittingExport(false);
    }
  };

  const handleDownload = async (job: ExportJob) => {
    try {
      const suffix =
        job.export_type === 'fhir_only'
          ? '.fhir.json'
          : job.export_type === 'catalog_only'
          ? '.catalog.json'
          : '.zip';
      await downloadExportFile(job.id, `${job.export_type}-${job.id}${suffix}`);
      toast.success('Download started');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Download failed');
    }
  };

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportMessage(null);
    setIsImporting(true);
    try {
      const job = await importBackupFile(file, autoMapBiomarkers, useAiNormalization);
      setImportJobs((prev) => [job, ...prev]);
      pollImportJob(job.id);
      setImportMessage({
        type: 'success',
        text: `Import job ${job.id.slice(0, 8)} queued — processing ${file.name}`,
      });
    } catch (err: any) {
      setImportMessage({
        type: 'error',
        text: err.response?.data?.detail || err.message || 'Import failed to start',
      });
    } finally {
      setIsImporting(false);
      if (e.target) e.target.value = '';
    }
  };

  const handleConfirmImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    showConfirmation({
      title: 'Restore from backup?',
      message: `Importing ${file.name} will restore data into the current tenant (${user?.tenant_id?.slice(0, 8) ?? '…'}). Existing records with the same id will be updated; new ids will be remapped. Telemetry, integration configs and (if included) documents will also be restored. This cannot be undone.`,
      confirmLabel: 'Restore Backup',
      cancelLabel: 'Cancel',
      confirmVariant: 'danger',
      onConfirm: () => handleImportFile(e),
    });
  };

  const formatBytes = (bytes?: number | null) => {
    if (!bytes) return '—';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  const formatDate = (iso?: string | null) => {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  const renderStatusBadge = (status: JobStatus) => (
    <span
      className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${JOB_STATUS_COLORS[status]}`}
    >
      {status}
    </span>
  );

  const renderProgress = (progress: number, status: JobStatus) => {
    if (TERMINAL_STATUSES.includes(status) && status !== 'PARTIAL') return null;
    return (
      <div className="mt-2 h-1.5 bg-blue-200/50 dark:bg-blue-900/20 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-600 transition-all duration-1000 ease-out"
          style={{ width: `${Math.max(2, progress)}%` }}
        />
      </div>
    );
  };

  const isExportFormDisabled =
    isSubmittingExport ||
    (scope === 'patient' && patientIds.length === 0) ||
    (scope === 'group' && patientIds.length === 0);

  return (
    <div className="max-w-7xl mx-auto space-y-6 animate-in fade-in duration-500">
      <PageHeader
        title="Export & Import (Backup)"
        subtitle="Export data as FHIR Bundles or full backups; restore from a backup archive"
        icon={<Database className="w-8 h-8" />}
        showBackButton
      />

      <StickyToolbar
        actions={
          <button
            onClick={() => fetchExportJobs(true)}
            disabled={isRefreshing}
            className="flex items-center space-x-2 px-4 py-2 bg-white dark:bg-dark-border text-gray-700 dark:text-dark-text rounded-lg border border-gray-200 dark:border-dark-border text-sm font-medium hover:bg-gray-50 dark:hover:bg-dark-bg/50 transition-all disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} />
            <span>{t('common.refresh', 'Refresh')}</span>
          </button>
        }
      />

      {/* Tab switcher (inline pattern from AIConfig) */}
      <div className="flex space-x-2 border-b border-gray-200 dark:border-dark-border">
        <button
          onClick={() => setActiveTab('export')}
          className={`px-4 py-2 font-medium rounded-t-lg ${
            activeTab === 'export'
              ? 'bg-blue-600 text-white'
              : 'bg-gray-100 dark:bg-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-200 dark:hover:bg-dark-bg/50'
          }`}
        >
          <Download className="w-4 h-4 inline mr-2" />
          Export
        </button>
        <button
          onClick={() => setActiveTab('import')}
          className={`px-4 py-2 font-medium rounded-t-lg ${
            activeTab === 'import'
              ? 'bg-blue-600 text-white'
              : 'bg-gray-100 dark:bg-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-200 dark:hover:bg-dark-bg/50'
          }`}
        >
          <Upload className="w-4 h-4 inline mr-2" />
          Import
        </button>
      </div>

      {activeTab === 'export' && (
        <div className="space-y-6">
          {/* Export configuration card */}
          <div className="bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border shadow-sm">
            <div className="flex items-center space-x-3 mb-4">
              <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
                <FileArchive className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              </div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">
                Create new export
              </h3>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Scope */}
              <div>
                <label className="block text-xs font-bold uppercase tracking-widest text-gray-500 dark:text-dark-muted mb-2">
                  Scope
                </label>
                <select
                  value={scope}
                  onChange={(e) => handleScopeChange(e.target.value as ExportScope)}
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
                >
                  <option value="patient">{EXPORT_SCOPE_LABELS.patient}</option>
                  <option value="group" disabled={!canUseGroupScope}>
                    {EXPORT_SCOPE_LABELS.group}
                    {!canUseGroupScope ? ' (requires MANAGER+)' : ''}
                  </option>
                  <option value="system" disabled={!canUseSystemScope}>
                    {EXPORT_SCOPE_LABELS.system}
                    {!canUseSystemScope ? ' (requires ADMIN+)' : ''}
                  </option>
                </select>
                <p className="mt-1 text-[11px] text-gray-400">
                  SMART scope:{' '}
                  <code className="bg-gray-100 dark:bg-dark-border px-1 rounded">
                    {scope === 'patient'
                      ? 'patient/*.rs'
                      : scope === 'group'
                      ? 'system/*.rs'
                      : 'system/*.cruds'}
                  </code>
                </p>
              </div>

              {/* Export type */}
              <div>
                <label className="block text-xs font-bold uppercase tracking-widest text-gray-500 dark:text-dark-muted mb-2">
                  Format
                </label>
                <select
                  value={exportType}
                  onChange={(e) => setExportType(e.target.value as ExportType)}
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-none dark:text-dark-text"
                >
                  <option value="fhir_only">{EXPORT_TYPE_LABELS.fhir_only}</option>
                  <option value="full_backup">{EXPORT_TYPE_LABELS.full_backup}</option>
                  <option value="catalog_only">{EXPORT_TYPE_LABELS.catalog_only}</option>
                </select>
                <p className="mt-1 text-[11px] text-gray-400">
                  {exportType === 'fhir_only' &&
                    'Portable FHIR R4B Bundle — import into other FHIR systems.'}
                  {exportType === 'full_backup' &&
                    'BagIt-style ZIP: FHIR + telemetry + documents + integration configs.'}
                  {exportType === 'catalog_only' &&
                    'Biomarker/unit + clinical-event-type definitions (cross-tenant).'}
                </p>
              </div>
            </div>

            {/* Patient selection (patient/group scope) */}
            {(scope === 'patient' || scope === 'group') && (
              <div className="mt-6">
                <label className="block text-xs font-bold uppercase tracking-widest text-gray-500 dark:text-dark-muted mb-2">
                  {scope === 'patient' ? 'Patient' : 'Patients (select one or more)'}
                </label>
                {scope === 'patient' && role === 'USER' ? (
                  <div className="text-sm text-gray-600 dark:text-dark-muted px-4 py-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-200 dark:border-dark-border">
                    {currentPatient
                      ? `Exporting your linked patient: ${currentPatient.name?.family ?? currentPatient.id}`
                      : 'No patient context selected — pick a patient from the header first.'}
                  </div>
                ) : availablePatients.length === 0 ? (
                  <div className="text-sm text-gray-400 px-4 py-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-200 dark:border-dark-border">
                    No patients available in this tenant.
                  </div>
                ) : (
                  <div className="max-h-48 overflow-y-auto border border-gray-200 dark:border-dark-border rounded-xl divide-y divide-gray-100 dark:divide-dark-border">
                    {availablePatients.map((p) => {
                      const checked = patientIds.includes(p.id);
                      return (
                        <label
                          key={p.id}
                          className="flex items-center space-x-3 px-4 py-2.5 cursor-pointer hover:bg-gray-50 dark:hover:bg-dark-bg/50"
                        >
                          <input
                            type={scope === 'patient' ? 'radio' : 'checkbox'}
                            name={scope === 'patient' ? 'patient-export-select' : undefined}
                            checked={checked}
                            onChange={() => handlePatientToggle(p.id)}
                            className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                          />
                          <span className="text-sm text-gray-900 dark:text-dark-text">
                            {p.name?.family ?? 'Unknown'}
                            {p.name?.given?.length
                              ? `, ${p.name.given.join(' ')}`
                              : ''}
                            {p.mrn ? (
                              <span className="ml-2 text-[11px] text-gray-400">
                                MRN: {p.mrn}
                              </span>
                            ) : null}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* Options (full_backup only) */}
            {exportType === 'full_backup' && (
              <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-4">
                <label className="flex items-center space-x-3 px-4 py-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-200 dark:border-dark-border cursor-pointer">
                  <input
                    type="checkbox"
                    checked={includeDocuments}
                    onChange={(e) => setIncludeDocuments(e.target.checked)}
                    className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                  />
                  <span className="text-sm text-gray-900 dark:text-dark-text">
                    Include uploaded documents (raw files)
                  </span>
                </label>
                <label className="flex items-center space-x-3 px-4 py-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-200 dark:border-dark-border cursor-pointer">
                  <input
                    type="checkbox"
                    checked={includeTelemetry}
                    onChange={(e) => setIncludeTelemetry(e.target.checked)}
                    className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                  />
                  <span className="text-sm text-gray-900 dark:text-dark-text">
                    Include telemetry{' '}
                    {scope === 'patient' && (
                      <span className="text-[11px] text-amber-600">
                        (excluded for patient scope — no patient_id on hypertable)
                      </span>
                    )}
                  </span>
                </label>
                <label className="flex items-center space-x-3 px-4 py-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-200 dark:border-dark-border cursor-pointer">
                  <input
                    type="checkbox"
                    checked={includeIntegrations}
                    onChange={(e) => setIncludeIntegrations(e.target.checked)}
                    className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                  />
                  <span className="text-sm text-gray-900 dark:text-dark-text">
                    Include integration configs (encrypted secrets + OAuth tokens)
                  </span>
                </label>
                {canUseAiConfig && scope === 'system' && (
                  <label className="flex items-center space-x-3 px-4 py-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-200 dark:border-dark-border cursor-pointer">
                    <input
                      type="checkbox"
                      checked={includeAiConfig}
                      onChange={(e) => setIncludeAiConfig(e.target.checked)}
                      className="w-4 h-4 text-blue-600 focus:ring-blue-500"
                    />
                    <span className="text-sm text-gray-900 dark:text-dark-text">
                      Include AI provider configs (export-only — not restored)
                    </span>
                  </label>
                )}
              </div>
            )}

            {/* Submit */}
            <div className="mt-6">
              <button
                onClick={handleExportSubmit}
                disabled={isExportFormDisabled}
                className="flex items-center justify-center space-x-2 px-6 py-3 bg-blue-600 text-white rounded-xl font-bold text-sm hover:bg-blue-700 transition-all disabled:opacity-50"
              >
                {isSubmittingExport ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Download className="w-4 h-4" />
                )}
                <span>{isSubmittingExport ? 'Starting export…' : 'Start export'}</span>
              </button>
            </div>
          </div>

          {/* Export jobs table */}
          <div className="bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border shadow-sm">
            <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text mb-4">
              Export jobs
            </h3>
            {exportJobs.length === 0 ? (
              <p className="text-sm text-gray-400 py-8 text-center">No export jobs yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[11px] uppercase tracking-widest text-gray-400 border-b border-gray-100 dark:border-dark-border">
                      <th className="py-2 pr-4">Created</th>
                      <th className="py-2 pr-4">Scope</th>
                      <th className="py-2 pr-4">Type</th>
                      <th className="py-2 pr-4">Status</th>
                      <th className="py-2 pr-4">Size</th>
                      <th className="py-2 pr-4">Counts</th>
                      <th className="py-2 pr-4 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                    {exportJobs.map((job) => (
                      <tr key={job.id} className="align-top">
                        <td className="py-3 pr-4 text-xs text-gray-600 dark:text-dark-muted">
                          {formatDate(job.created_at)}
                        </td>
                        <td className="py-3 pr-4">
                          <span className="text-xs font-medium text-gray-700 dark:text-dark-text">
                            {EXPORT_SCOPE_LABELS[job.scope]}
                          </span>
                        </td>
                        <td className="py-3 pr-4 text-xs text-gray-700 dark:text-dark-text">
                          {EXPORT_TYPE_LABELS[job.export_type]}
                        </td>
                        <td className="py-3 pr-4">
                          {renderStatusBadge(job.status)}
                          {renderProgress(job.progress, job.status)}
                        </td>
                        <td className="py-3 pr-4 text-xs text-gray-600 dark:text-dark-muted">
                          {formatBytes(job.file_size_bytes)}
                        </td>
                        <td className="py-3 pr-4 text-[11px] text-gray-500 dark:text-dark-muted">
                          {job.resource_counts &&
                          Object.keys(job.resource_counts).length > 0
                            ? Object.entries(job.resource_counts)
                                .map(([k, v]) => `${k}: ${v}`)
                                .join(', ')
                            : '—'}
                        </td>
                        <td className="py-3 pr-4 text-right">
                          <button
                            onClick={() => setSelectedExportJob(job)}
                            className="inline-flex items-center space-x-1 px-3 py-1.5 bg-gray-50 dark:bg-gray-800 text-gray-700 dark:text-gray-300 rounded-lg text-xs font-bold hover:bg-gray-100 dark:hover:bg-gray-700 transition-all mr-2"
                          >
                            <span>Details</span>
                          </button>
                          {job.status === 'COMPLETED' && job.file_path && (
                            <button
                              onClick={() => handleDownload(job)}
                              className="inline-flex items-center space-x-1 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded-lg text-xs font-bold hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-all"
                            >
                              <Download className="w-3 h-3" />
                              <span>Download</span>
                            </button>
                          )}
                          {job.status === 'FAILED' && job.error_message && (
                            <span
                              title={job.error_message}
                              className="inline-flex items-center space-x-1 text-xs text-red-600 dark:text-red-400"
                            >
                              <AlertCircle className="w-3 h-3" />
                              <span>error</span>
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'import' && (
        <div className="space-y-6">
          <div className="bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border shadow-sm">
            <div className="flex items-center space-x-3 mb-4">
              <div className="p-2 bg-purple-50 dark:bg-purple-900/30 rounded-xl">
                <Upload className="w-5 h-5 text-purple-600 dark:text-purple-400" />
              </div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">
                Restore from backup
              </h3>
            </div>

            <div className="mb-4 p-4 rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-900/30 flex items-start space-x-3">
              <Info className="w-5 h-5 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" />
              <div className="text-xs text-blue-700 dark:text-blue-300 space-y-1">
                <p>
                  Accepted formats: <strong>ZIP</strong> (full backup),{' '}
                  <strong>FHIR Bundle .json</strong>, or{' '}
                  <strong>catalog .json</strong>.
                </p>
                <p>
                  Restore target: tenant <code>{user?.tenant_id?.slice(0, 8) ?? '…'}</code>.
                  Existing records with the same id are updated; new ids are remapped
                  automatically.
                </p>
                {includeIntegrations && (
                  <p>
                    Integration secrets are Fernet-encrypted — restoring on a deployment
                    with a different <code>INTEGRATION_SECRET_KEY</code> will leave secrets
                    undecryptable.
                  </p>
                )}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
              <label className="flex items-start space-x-3 px-4 py-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-200 dark:border-dark-border cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoMapBiomarkers}
                  onChange={(e) => setAutoMapBiomarkers(e.target.checked)}
                  className="w-4 h-4 mt-0.5 text-blue-600 focus:ring-blue-500"
                />
                <div className="text-sm text-gray-900 dark:text-dark-text">
                  <span className="font-bold block mb-0.5">Auto-map Biomarkers</span>
                  <span className="text-xs text-gray-500 dark:text-dark-muted">Automatically link imported FHIR observations to your system catalog.</span>
                </div>
              </label>
              
              <div className={`flex items-start space-x-3 px-4 py-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-200 dark:border-dark-border ${!autoMapBiomarkers ? 'opacity-50' : ''}`}>
                <label className="flex items-start cursor-pointer mt-0.5">
                  <input
                    type="checkbox"
                    checked={useAiNormalization}
                    onChange={(e) => setUseAiNormalization(e.target.checked)}
                    disabled={!autoMapBiomarkers}
                    className="w-4 h-4 text-blue-600 focus:ring-blue-500 disabled:opacity-50 cursor-pointer"
                  />
                </label>
                <div className="text-sm text-gray-900 dark:text-dark-text flex-1">
                  <div className="flex items-center space-x-2 mb-0.5">
                    <label 
                      className="font-bold cursor-pointer" 
                      onClick={() => {
                         if (autoMapBiomarkers) {
                           setUseAiNormalization(!useAiNormalization);
                         }
                      }}
                    >
                      Use AI Normalization
                    </label>
                    <div className="inline-flex">
                      <AIBadge workflow="fhir_import_normalization" />
                    </div>
                  </div>
                  <span className="text-xs text-gray-500 dark:text-dark-muted block">Use AI to generate standard LOINC codes and names for unknown imported data.</span>
                </div>
              </div>
            </div>

            <div className="border-2 border-dashed border-gray-200 dark:border-dark-border rounded-2xl p-8 text-center hover:bg-gray-50 dark:hover:bg-dark-bg/50 transition-colors relative">
              <input
                type="file"
                accept=".zip,.json"
                onChange={handleConfirmImport}
                disabled={isImporting}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
              />
              {isImporting ? (
                <Loader2 className="w-8 h-8 text-blue-600 mx-auto mb-3 animate-spin" />
              ) : (
                <Upload className="w-8 h-8 text-gray-400 mx-auto mb-3" />
              )}
              <p className="text-sm font-bold text-gray-700 dark:text-dark-text">
                Click or drop a backup file to restore
              </p>
              <p className="text-[10px] text-gray-400 uppercase tracking-widest mt-1">
                ZIP or JSON · max {''}50 MB
              </p>
            </div>

            {importMessage && (
              <div
                className={`mt-4 p-4 rounded-xl flex items-start space-x-3 ${
                  importMessage.type === 'success'
                    ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-900/30'
                    : importMessage.type === 'error'
                    ? 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-900/30'
                    : 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400 border border-blue-200 dark:border-blue-900/30'
                }`}
              >
                {importMessage.type === 'success' ? (
                  <CheckCircle2 className="w-5 h-5 flex-shrink-0" />
                ) : importMessage.type === 'error' ? (
                  <AlertCircle className="w-5 h-5 flex-shrink-0" />
                ) : (
                  <Info className="w-5 h-5 flex-shrink-0" />
                )}
                <div className="flex-1">
                  <h4 className="font-bold text-sm">
                    {importMessage.type === 'success'
                      ? 'Import queued'
                      : importMessage.type === 'error'
                      ? 'Import failed'
                      : 'Info'}
                  </h4>
                  <p className="text-xs opacity-90">{importMessage.text}</p>
                </div>
                <button
                  onClick={() => setImportMessage(null)}
                  className="opacity-60 hover:opacity-100"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            )}
          </div>

          {/* Import jobs table */}
          <div className="bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border shadow-sm">
            <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text mb-4">
              Import jobs
            </h3>
            {importJobs.length === 0 ? (
              <p className="text-sm text-gray-400 py-8 text-center">No import jobs yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[11px] uppercase tracking-widest text-gray-400 border-b border-gray-100 dark:border-dark-border">
                      <th className="py-2 pr-4">Created</th>
                      <th className="py-2 pr-4">Source</th>
                      <th className="py-2 pr-4">Status</th>
                      <th className="py-2 pr-4">Records</th>
                      <th className="py-2 pr-4">Verification</th>
                      <th className="py-2 pr-4">Errors / warnings</th>
                      <th className="py-2 pr-4 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                    {importJobs.map((job) => (
                      <tr key={job.id} className="align-top">
                        <td className="py-3 pr-4 text-xs text-gray-600 dark:text-dark-muted">
                          {formatDate(job.created_at)}
                        </td>
                        <td className="py-3 pr-4 text-xs text-gray-700 dark:text-dark-text">
                          {job.source_filename || '—'}
                        </td>
                        <td className="py-3 pr-4">
                          {renderStatusBadge(job.status)}
                          {renderProgress(job.progress, job.status)}
                        </td>
                        <td className="py-3 pr-4 text-xs text-gray-600 dark:text-dark-muted">
                          {job.processed_records ?? 0} processed / {job.failed_records ?? 0}{' '}
                          failed
                        </td>
                        <td className="py-3 pr-4 text-[11px]">
                          {job.restore_result ? (
                            <div className="space-y-0.5">
                              <div
                                className={
                                  job.restore_result.manifest_verified
                                    ? 'text-green-600 dark:text-green-400'
                                    : 'text-red-600 dark:text-red-400'
                                }
                              >
                                {job.restore_result.manifest_verified ? '✓' : '✗'} manifest
                              </div>
                              <div
                                className={
                                  job.restore_result.fhir_validated
                                    ? 'text-green-600 dark:text-green-400'
                                    : 'text-red-600 dark:text-red-400'
                                }
                              >
                                {job.restore_result.fhir_validated ? '✓' : '✗'} FHIR
                              </div>
                            </div>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td className="py-3 pr-4 text-[11px] text-gray-500 dark:text-dark-muted max-w-xs">
                          {job.error_message && (
                            <div className="text-red-600 dark:text-red-400 truncate" title={job.error_message}>
                              {job.error_message}
                            </div>
                          )}
                          {(job.warnings?.length ?? 0) > 0 && (
                            <div className="text-amber-600 dark:text-amber-400 truncate">
                              {job.warnings!.length} warning(s)
                            </div>
                          )}
                          {(job.errors?.length ?? 0) > 0 && (
                            <div className="text-red-600 dark:text-red-400 truncate">
                              {job.errors!.length} error(s)
                            </div>
                          )}
                          {!job.error_message &&
                            !(job.errors?.length ?? 0) &&
                            !(job.warnings?.length ?? 0) &&
                            '—'}
                        </td>
                        <td className="py-3 pr-4 text-right">
                          <button
                            onClick={() => setSelectedImportJob(job)}
                            className="inline-flex items-center space-x-1 px-3 py-1.5 bg-gray-50 dark:bg-gray-800 text-gray-700 dark:text-gray-300 rounded-lg text-xs font-bold hover:bg-gray-100 dark:hover:bg-gray-700 transition-all"
                          >
                            <span>Details</span>
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Export Job Details Modal */}
      <Modal
        isOpen={!!selectedExportJob}
        onClose={() => setSelectedExportJob(null)}
        title="Export Job Details"
      >
        {selectedExportJob && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">Status</span>
                <span className="font-medium">{selectedExportJob.status}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">Type</span>
                <span className="font-medium">{EXPORT_TYPE_LABELS[selectedExportJob.export_type]}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">Scope</span>
                <span className="font-medium">{EXPORT_SCOPE_LABELS[selectedExportJob.scope]}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">Size</span>
                <span className="font-medium">{formatBytes(selectedExportJob.file_size_bytes)}</span>
              </div>
            </div>

            {selectedExportJob.error_message && (
              <div>
                <span className="text-gray-500 dark:text-dark-muted block mb-1">Error Message</span>
                <div className="bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 p-3 rounded-lg text-xs break-words">
                  {selectedExportJob.error_message}
                </div>
              </div>
            )}

            {selectedExportJob.resource_counts && Object.keys(selectedExportJob.resource_counts).length > 0 && (
              <div>
                <span className="text-gray-500 dark:text-dark-muted block mb-2">Resource Counts</span>
                <div className="grid grid-cols-2 gap-2 text-sm bg-gray-50 dark:bg-dark-bg p-4 rounded-xl border border-gray-100 dark:border-dark-border">
                  {Object.entries(selectedExportJob.resource_counts).map(([key, val]) => (
                    <div key={key} className="flex justify-between">
                      <span className="text-gray-600 dark:text-dark-muted">{key}</span>
                      <span className="font-bold">{val}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* Import Job Details Modal */}
      <Modal
        isOpen={!!selectedImportJob}
        onClose={() => setSelectedImportJob(null)}
        title="Import Job Details"
      >
        {selectedImportJob && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">Status</span>
                <span className="font-medium">{selectedImportJob.status}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">Source File</span>
                <span className="font-medium break-all">{selectedImportJob.source_filename || '—'}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">Records Processed</span>
                <span className="font-medium">{selectedImportJob.processed_records ?? 0}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">Records Failed</span>
                <span className="font-medium">{selectedImportJob.failed_records ?? 0}</span>
              </div>
            </div>

            {selectedImportJob.error_message && (
              <div>
                <span className="text-gray-500 dark:text-dark-muted block mb-1">Fatal Error</span>
                <div className="bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 p-3 rounded-lg text-xs break-words">
                  {selectedImportJob.error_message}
                </div>
              </div>
            )}

            {selectedImportJob.restore_result && (
              <div className="grid grid-cols-2 gap-4">
                {selectedImportJob.restore_result.created_resources && Object.keys(selectedImportJob.restore_result.created_resources).length > 0 && (
                  <div>
                    <span className="text-gray-500 dark:text-dark-muted block mb-2 text-sm font-bold">Created</span>
                    <ul className="text-sm space-y-1 bg-green-50 dark:bg-green-900/10 p-3 rounded-lg border border-green-100 dark:border-green-900/30">
                      {Object.entries(selectedImportJob.restore_result.created_resources).map(([k, v]) => (
                        <li key={k} className="flex justify-between">
                          <span>{k}</span>
                          <span className="font-bold text-green-700 dark:text-green-400">{v}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {selectedImportJob.restore_result.updated_resources && Object.keys(selectedImportJob.restore_result.updated_resources).length > 0 && (
                  <div>
                    <span className="text-gray-500 dark:text-dark-muted block mb-2 text-sm font-bold">Updated</span>
                    <ul className="text-sm space-y-1 bg-blue-50 dark:bg-blue-900/10 p-3 rounded-lg border border-blue-100 dark:border-blue-900/30">
                      {Object.entries(selectedImportJob.restore_result.updated_resources).map(([k, v]) => (
                        <li key={k} className="flex justify-between">
                          <span>{k}</span>
                          <span className="font-bold text-blue-700 dark:text-blue-400">{v}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {selectedImportJob.errors && selectedImportJob.errors.length > 0 && (
              <div>
                <span className="text-red-600 dark:text-red-400 block mb-2 text-sm font-bold border-b border-red-100 dark:border-red-900/30 pb-1">
                  Errors ({selectedImportJob.errors.length})
                </span>
                <ul className="space-y-2 max-h-48 overflow-y-auto custom-scrollbar">
                  {selectedImportJob.errors.map((err, i) => (
                    <li key={i} className="text-xs bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 p-2 rounded">
                      {err}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {selectedImportJob.warnings && selectedImportJob.warnings.length > 0 && (
              <div>
                <span className="text-amber-600 dark:text-amber-400 block mb-2 text-sm font-bold border-b border-amber-100 dark:border-amber-900/30 pb-1">
                  Warnings ({selectedImportJob.warnings.length})
                </span>
                <ul className="space-y-2 max-h-48 overflow-y-auto custom-scrollbar">
                  {selectedImportJob.warnings.map((warn, i) => (
                    <li key={i} className="text-xs bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400 p-2 rounded">
                      {warn}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
};

export default ExportImport;
