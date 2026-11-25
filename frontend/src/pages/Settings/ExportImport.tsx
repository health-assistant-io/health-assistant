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
import { ActiveTaskBadge } from '../../components/ui/ActiveTaskBadge';
import {
  createExportJob,
  listExportJobs,
  getExportJob,
  downloadExportFile,
  importBackupFile,
  getImportJob,
} from '../../services/backupService';
import { listPatients } from '../../services/patientService';
import type {
  BackupRequest,
  ExportJob,
  ExportScope,
  ExportType,
  ImportJob,
  JobStatus,
} from '../../types/backup';
import {
  EXPORT_SCOPE_LABEL_KEYS,
  EXPORT_TYPE_LABEL_KEYS,
  JOB_STATUS_COLORS,
  JOB_STATUS_LABEL_KEYS,
  TERMINAL_STATUSES,
} from '../../types/backup';
import type { Patient } from '../../types/patient';

import { formatTimestamp } from '../../lib/dateFormats';
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
      toast.error(err.response?.data?.detail || t('export_import.toast_load_failed'));
    } finally {
      if (showSpinner) setIsRefreshing(false);
    }
  }, [t]);

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
            toast.success(
              t('export_import.toast_export_ready', {
                type: t(EXPORT_TYPE_LABEL_KEYS[job.export_type]),
              })
            );
          } else if (job.status === 'FAILED') {
            toast.error(job.error_message || t('export_import.toast_export_failed'));
          } else if (job.status === 'PARTIAL') {
            toast.warn(t('export_import.toast_export_warnings'));
          }
        }
        if (Date.now() - startedAt > STALL_TIMEOUT_MS) {
          if (pollTimers.current[jobId]) {
            clearInterval(pollTimers.current[jobId]);
            delete pollTimers.current[jobId];
          }
          toast.warn(t('export_import.toast_export_timeout'));
        }
      } catch (err) {
        console.error('Polling export job failed', err);
      }
    }, POLL_INTERVAL_MS);
  }, [t]);

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
              t('export_import.toast_import_complete', {
                count: job.processed_records ?? 0,
              })
            );
          } else if (job.status === 'FAILED') {
            toast.error(job.error_message || t('export_import.import_failed'));
          } else if (job.status === 'PARTIAL') {
            toast.warn(
              t('export_import.toast_import_partial', {
                count: job.failed_records ?? 0,
              })
            );
          }
        }
        if (Date.now() - startedAt > STALL_TIMEOUT_MS) {
          if (pollTimers.current[key]) {
            clearInterval(pollTimers.current[key]);
            delete pollTimers.current[key];
          }
          toast.warn(t('export_import.toast_import_timeout'));
        }
      } catch (err) {
        console.error('Polling import job failed', err);
      }
    }, POLL_INTERVAL_MS);
  }, [t]);

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
    listPatients(undefined, 200)
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
      toast.error(t('export_import.toast_group_role'));
      return;
    }
    if (next === 'system' && !canUseSystemScope) {
      toast.error(t('export_import.toast_system_role'));
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
      toast.error(t('export_import.toast_select_patient'));
      return;
    }
    if (scope === 'patient' && role === 'USER' && patientIds.length > 1) {
      toast.error(t('export_import.toast_user_single'));
      return;
    }
    if (scope === 'group' && patientIds.length === 0) {
      toast.error(t('export_import.toast_select_group'));
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
      toast.success(t('export_import.toast_export_started'));
    } catch (err: any) {
      toast.error(err.response?.data?.detail || t('export_import.toast_start_failed'));
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
      toast.success(t('export_import.toast_download_started'));
    } catch (err: any) {
      toast.error(err.response?.data?.detail || t('export_import.toast_download_failed'));
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
        text: t('export_import.import_queued_detail', {
          id: job.id.slice(0, 8),
          file: file.name,
        }),
      });
    } catch (err: any) {
      setImportMessage({
        type: 'error',
        text: err.response?.data?.detail || err.message || t('export_import.toast_import_start_failed'),
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
      title: t('export_import.confirm_restore_title'),
      message: t('export_import.confirm_restore_message', {
        file: file.name,
        tenant: user?.tenant_id?.slice(0, 8) ?? '…',
      }),
      confirmLabel: t('export_import.confirm_restore_ok'),
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
      return formatTimestamp(new Date(iso));
    } catch {
      return iso;
    }
  };

  const renderStatusBadge = (status: JobStatus) => (
    <span
      className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${JOB_STATUS_COLORS[status]}`}
    >
      {t(JOB_STATUS_LABEL_KEYS[status])}
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
        title={t('export_import.title')}
        subtitle={t('export_import.subtitle')}
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
          <Download className="w-4 h-4 inline mr-2" />{t('export_import.tab_export')}</button>
        <button
          onClick={() => setActiveTab('import')}
          className={`px-4 py-2 font-medium rounded-t-lg ${
            activeTab === 'import'
              ? 'bg-blue-600 text-white'
              : 'bg-gray-100 dark:bg-dark-border text-gray-700 dark:text-dark-text hover:bg-gray-200 dark:hover:bg-dark-bg/50'
          }`}
        >
          <Upload className="w-4 h-4 inline mr-2" />{t('export_import.tab_import')}</button>
      </div>

      {activeTab === 'export' && (
        <div className="space-y-6">
          {/* Export configuration card */}
          <div className="bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border shadow-xs">
            <div className="flex items-center space-x-3 mb-4">
              <div className="p-2 bg-blue-50 dark:bg-blue-900/30 rounded-xl">
                <FileArchive className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              </div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{t('export_import.create_export')}</h3>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Scope */}
              <div>
                <label className="block text-xs font-bold uppercase tracking-widest text-gray-500 dark:text-dark-muted mb-2">
                  {t('export_import.scope')}
                </label>
                <select
                  value={scope}
                  onChange={(e) => handleScopeChange(e.target.value as ExportScope)}
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-hidden dark:text-dark-text"
                >
                  <option value="patient">{t(EXPORT_SCOPE_LABEL_KEYS.patient)}</option>
                  <option value="group" disabled={!canUseGroupScope}>
                    {t(EXPORT_SCOPE_LABEL_KEYS.group)}
                    {!canUseGroupScope ? t('export_import.scope_group_locked') : ''}
                  </option>
                  <option value="system" disabled={!canUseSystemScope}>
                    {t(EXPORT_SCOPE_LABEL_KEYS.system)}
                    {!canUseSystemScope ? t('export_import.scope_system_locked') : ''}
                  </option>
                </select>
                <p className="mt-1 text-[11px] text-gray-400">
                  {t('export_import.smart_scope')}{' '}
                  <code className="bg-gray-100 dark:bg-dark-border px-1 rounded-sm">
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
                  {t('export_import.format')}
                </label>
                <select
                  value={exportType}
                  onChange={(e) => setExportType(e.target.value as ExportType)}
                  className="w-full px-4 py-3 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl text-sm focus:ring-2 focus:ring-blue-500 outline-hidden dark:text-dark-text"
                >
                  <option value="fhir_only">{t(EXPORT_TYPE_LABEL_KEYS.fhir_only)}</option>
                  <option value="full_backup">{t(EXPORT_TYPE_LABEL_KEYS.full_backup)}</option>
                  <option value="catalog_only">{t(EXPORT_TYPE_LABEL_KEYS.catalog_only)}</option>
                </select>
                <p className="mt-1 text-[11px] text-gray-400">
                  {exportType === 'fhir_only' && t('export_import.type_fhir_only_hint')}
                  {exportType === 'full_backup' && t('export_import.type_full_backup_hint')}
                  {exportType === 'catalog_only' && t('export_import.type_catalog_only_hint')}
                </p>
              </div>
            </div>

            {/* Patient selection (patient/group scope) */}
            {(scope === 'patient' || scope === 'group') && (
              <div className="mt-6">
                <label className="block text-xs font-bold uppercase tracking-widest text-gray-500 dark:text-dark-muted mb-2">
                  {scope === 'patient'
                    ? t('export_import.patient')
                    : t('export_import.patients_select')}
                </label>
                {scope === 'patient' && role === 'USER' ? (
                  <div className="text-sm text-gray-600 dark:text-dark-muted px-4 py-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-200 dark:border-dark-border">
                    {currentPatient
                      ? t('export_import.exporting_linked', {
                          name: currentPatient.name?.family ?? currentPatient.id,
                        })
                      : t('export_import.no_patient_context')}
                  </div>
                ) : availablePatients.length === 0 ? (
                  <div className="text-sm text-gray-400 px-4 py-3 bg-gray-50 dark:bg-dark-bg rounded-xl border border-gray-200 dark:border-dark-border">{t('export_import.no_patients')}</div>
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
                            {p.name?.family ?? t('export_import.unknown')}
                            {p.name?.given?.length
                              ? `, ${p.name.given.join(' ')}`
                              : ''}
                            {p.mrn ? (
                              <span className="ml-2 text-[11px] text-gray-400">
                                {t('export_import.mrn')} {p.mrn}
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
                    {t('export_import.include_documents')}
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
                    {t('export_import.include_telemetry')}{' '}
                    {scope === 'patient' && (
                      <span className="text-[11px] text-amber-600">
                        {t('export_import.telemetry_excluded_hint')}
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
                    {t('export_import.include_integrations')}
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
                      {t('export_import.include_ai_config')}
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
                <span>{isSubmittingExport
                    ? t('export_import.starting_export')
                    : t('export_import.start_export')}</span>
              </button>
            </div>
          </div>

          {/* Export jobs table */}
          <div className="bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border shadow-xs">
            <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text mb-4">{t('export_import.export_jobs')}</h3>
            {exportJobs.length === 0 ? (
              <p className="text-sm text-gray-400 py-8 text-center">{t('export_import.export_empty')}</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[11px] uppercase tracking-widest text-gray-400 border-b border-gray-100 dark:border-dark-border">
                      <th className="py-2 pr-4">{t('export_import.col_created')}</th>
                      <th className="py-2 pr-4">{t('export_import.col_scope')}</th>
                      <th className="py-2 pr-4">{t('export_import.col_type')}</th>
                      <th className="py-2 pr-4">{t('export_import.col_status')}</th>
                      <th className="py-2 pr-4">{t('export_import.col_size')}</th>
                      <th className="py-2 pr-4">{t('export_import.col_counts')}</th>
                      <th className="py-2 pr-4 text-right">{t('export_import.col_action')}</th>
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
                            {t(EXPORT_SCOPE_LABEL_KEYS[job.scope])}
                          </span>
                        </td>
                        <td className="py-3 pr-4 text-xs text-gray-700 dark:text-dark-text">
                          {t(EXPORT_TYPE_LABEL_KEYS[job.export_type])}
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
                            <span>{t('export_import.details')}</span>
                          </button>
                          {job.status === 'COMPLETED' && (
                            <button
                              onClick={() => handleDownload(job)}
                              className="inline-flex items-center space-x-1 px-3 py-1.5 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded-lg text-xs font-bold hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-all"
                            >
                              <Download className="w-3 h-3" />
                              <span>{t('export_import.download')}</span>
                            </button>
                          )}
                          {job.status === 'FAILED' && job.error_message && (
                            <span
                              title={job.error_message}
                              className="inline-flex items-center space-x-1 text-xs text-red-600 dark:text-red-400"
                            >
                              <AlertCircle className="w-3 h-3" />
                              <span>{t('export_import.error_badge')}</span>
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
          <div className="bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border shadow-xs">
            <div className="flex items-center space-x-3 mb-4">
              <div className="p-2 bg-purple-50 dark:bg-purple-900/30 rounded-xl">
                <Upload className="w-5 h-5 text-purple-600 dark:text-purple-400" />
              </div>
              <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text">{t('export_import.restore_title')}</h3>
            </div>

            <div className="mb-4 p-4 rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-900/30 flex items-start space-x-3">
              <Info className="w-5 h-5 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
              <div className="text-xs text-blue-700 dark:text-blue-300 space-y-1">
                <p>{t('export_import.accepted_formats')}</p>
                <p>
                  {t('export_import.restore_target', {
                    tenant: user?.tenant_id?.slice(0, 8) ?? '…',
                  })}
                </p>
                {includeIntegrations && (
                  <p>{t('export_import.integration_secrets_hint')}</p>
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
                  <span className="font-bold block mb-0.5">{t('export_import.auto_map')}</span>
                  <span className="text-xs text-gray-500 dark:text-dark-muted">{t('export_import.auto_map_hint')}</span>
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
                    >{t('export_import.ai_normalization')}</label>
                    <div className="inline-flex">
                      <ActiveTaskBadge workflow="fhir_import_normalization" />
                    </div>
                  </div>
                  <span className="text-xs text-gray-500 dark:text-dark-muted block">{t('export_import.ai_normalization_hint')}</span>
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
              <p className="text-sm font-bold text-gray-700 dark:text-dark-text">{t('export_import.drop_hint')}</p>
              <p className="text-[10px] text-gray-400 uppercase tracking-widest mt-1">
                {t('export_import.drop_formats')}
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
                  <CheckCircle2 className="w-5 h-5 shrink-0" />
                ) : importMessage.type === 'error' ? (
                  <AlertCircle className="w-5 h-5 shrink-0" />
                ) : (
                  <Info className="w-5 h-5 shrink-0" />
                )}
                <div className="flex-1">
                  <h4 className="font-bold text-sm">
                    {importMessage.type === 'success'
                      ? t('export_import.import_queued')
                      : importMessage.type === 'error'
                      ? t('export_import.import_failed')
                      : t('export_import.info')}
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
          <div className="bg-white dark:bg-dark-surface rounded-2xl p-6 border border-gray-100 dark:border-dark-border shadow-xs">
            <h3 className="text-lg font-bold text-gray-900 dark:text-dark-text mb-4">{t('export_import.import_jobs')}</h3>
            {importJobs.length === 0 ? (
              <p className="text-sm text-gray-400 py-8 text-center">{t('export_import.import_empty')}</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[11px] uppercase tracking-widest text-gray-400 border-b border-gray-100 dark:border-dark-border">
                      <th className="py-2 pr-4">{t('export_import.col_created')}</th>
                      <th className="py-2 pr-4">{t('export_import.col_source')}</th>
                      <th className="py-2 pr-4">{t('export_import.col_status')}</th>
                      <th className="py-2 pr-4">{t('export_import.col_records')}</th>
                      <th className="py-2 pr-4">{t('export_import.col_verification')}</th>
                      <th className="py-2 pr-4">{t('export_import.col_errors_warnings')}</th>
                      <th className="py-2 pr-4 text-right">{t('export_import.col_action')}</th>
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
                          {t('export_import.records_line', {
                            processed: job.processed_records ?? 0,
                            failed: job.failed_records ?? 0,
                          })}
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
                                {job.restore_result.manifest_verified ? '✓' : '✗'} {t('export_import.manifest')}
                              </div>
                              <div
                                className={
                                  job.restore_result.fhir_validated
                                    ? 'text-green-600 dark:text-green-400'
                                    : 'text-red-600 dark:text-red-400'
                                }
                              >
                                {job.restore_result.fhir_validated ? '✓' : '✗'} {t('export_import.fhir')}
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
                              {t('export_import.warnings_count', { count: job.warnings!.length })}
                            </div>
                          )}
                          {(job.errors?.length ?? 0) > 0 && (
                            <div className="text-red-600 dark:text-red-400 truncate">
                              {t('export_import.errors_count', { count: job.errors!.length })}
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
                            <span>{t('export_import.details')}</span>
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
        open={!!selectedExportJob}
        onOpenChange={(o) => !o && setSelectedExportJob(null)}
        title={t('export_import.export_job_details')}
      >
        {selectedExportJob && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">{t('export_import.col_status')}</span>
                <span className="font-medium">{selectedExportJob.status}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">{t('export_import.col_type')}</span>
                <span className="font-medium">{t(EXPORT_TYPE_LABEL_KEYS[selectedExportJob.export_type])}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">{t('export_import.col_scope')}</span>
                <span className="font-medium">{t(EXPORT_SCOPE_LABEL_KEYS[selectedExportJob.scope])}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">{t('export_import.col_size')}</span>
                <span className="font-medium">{formatBytes(selectedExportJob.file_size_bytes)}</span>
              </div>
            </div>

            {selectedExportJob.error_message && (
              <div>
                <span className="text-gray-500 dark:text-dark-muted block mb-1">{t('export_import.error_message')}</span>
                <div className="bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 p-3 rounded-lg text-xs wrap-break-word">
                  {selectedExportJob.error_message}
                </div>
              </div>
            )}

            {selectedExportJob.resource_counts && Object.keys(selectedExportJob.resource_counts).length > 0 && (
              <div>
                <span className="text-gray-500 dark:text-dark-muted block mb-2">{t('export_import.resource_counts')}</span>
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
        open={!!selectedImportJob}
        onOpenChange={(o) => !o && setSelectedImportJob(null)}
        title={t('export_import.import_job_details')}
      >
        {selectedImportJob && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">{t('export_import.col_status')}</span>
                <span className="font-medium">{selectedImportJob.status}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">{t('export_import.source_file')}</span>
                <span className="font-medium break-all">{selectedImportJob.source_filename || '—'}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">{t('export_import.records_processed')}</span>
                <span className="font-medium">{selectedImportJob.processed_records ?? 0}</span>
              </div>
              <div>
                <span className="text-gray-500 dark:text-dark-muted block">{t('export_import.records_failed')}</span>
                <span className="font-medium">{selectedImportJob.failed_records ?? 0}</span>
              </div>
            </div>

            {selectedImportJob.error_message && (
              <div>
                <span className="text-gray-500 dark:text-dark-muted block mb-1">{t('export_import.fatal_error')}</span>
                <div className="bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 p-3 rounded-lg text-xs wrap-break-word">
                  {selectedImportJob.error_message}
                </div>
              </div>
            )}

            {selectedImportJob.restore_result && (
              <div className="grid grid-cols-2 gap-4">
                {selectedImportJob.restore_result.created_resources && Object.keys(selectedImportJob.restore_result.created_resources).length > 0 && (
                  <div>
                    <span className="text-gray-500 dark:text-dark-muted block mb-2 text-sm font-bold">{t('export_import.created')}</span>
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
                    <span className="text-gray-500 dark:text-dark-muted block mb-2 text-sm font-bold">{t('export_import.updated')}</span>
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
                    <li key={i} className="text-xs bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 p-2 rounded-sm">
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
                    <li key={i} className="text-xs bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400 p-2 rounded-sm">
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
