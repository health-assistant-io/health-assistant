import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  createExportJob,
  listExportJobs,
  getExportJob,
  downloadExportFile,
  importBackupFile,
  getImportJob,
} from './backupService';
import api from '../api/axios';

vi.mock('../api/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('backupService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('createExportJob posts the BackupRequest body to /export', async () => {
    const mockJob = { id: '123', scope: 'patient', export_type: 'fhir_only', status: 'PENDING', progress: 0 };
    (api.post as any).mockResolvedValue({ data: mockJob });

    const result = await createExportJob({
      scope: 'patient',
      export_type: 'fhir_only',
      patient_ids: ['p1'],
    });

    expect(api.post).toHaveBeenCalledWith('/export', {
      scope: 'patient',
      export_type: 'fhir_only',
      patient_ids: ['p1'],
    });
    expect(result).toEqual(mockJob);
  });

  it('listExportJobs passes limit as a query param', async () => {
    (api.get as any).mockResolvedValue({ data: { items: [], total: 0 } });
    await listExportJobs(25);
    expect(api.get).toHaveBeenCalledWith('/export/jobs', { params: { limit: 25 } });
  });

  it('getExportJob hits the job status endpoint', async () => {
    (api.get as any).mockResolvedValue({ data: { id: 'abc', status: 'COMPLETED' } });
    const result = await getExportJob('abc');
    expect(api.get).toHaveBeenCalledWith('/export/jobs/abc');
    expect(result.id).toBe('abc');
  });

  it('downloadExportFile requests a blob with the right responseType', async () => {
    const blob = new Blob(['x'], { type: 'application/zip' });
    (api.get as any).mockResolvedValue({ data: blob });

    if (typeof window === 'undefined') {
      await downloadExportFile('job-1', 'backup.zip');
      expect(api.get).toHaveBeenCalledWith('/export/jobs/job-1/download', { responseType: 'blob' });
      return;
    }

    const createObjectURL = vi.fn(() => 'blob:fake-url');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(window.URL, 'createObjectURL', { value: createObjectURL, writable: true });
    Object.defineProperty(window.URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true });
    const clickSpy = vi.fn();
    const appendSpy = vi.spyOn(document.body, 'appendChild').mockImplementation((node) => node);
    const removeSpy = vi.spyOn(document.body, 'removeChild').mockImplementation((node) => node);
    // @ts-expect-error — click is a prototype method, spy on it directly
    vi.spyOn(HTMLAnchorElement.prototype, 'click', 'get').mockImplementation(() => clickSpy);

    await downloadExportFile('job-1', 'backup.zip');

    expect(api.get).toHaveBeenCalledWith('/export/jobs/job-1/download', { responseType: 'blob' });
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake-url');
    appendSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it('importBackupFile posts FormData with multipart content type', async () => {
    const file = new File(['PK'], 'backup.zip', { type: 'application/zip' });
    (api.post as any).mockResolvedValue({ data: { id: 'job-2', status: 'PENDING' } });

    const result = await importBackupFile(file);

    expect(api.post).toHaveBeenCalledWith(
      '/import/backup',
      expect.any(FormData),
      expect.objectContaining({ headers: { 'Content-Type': 'multipart/form-data' } })
    );
    const formData = (api.post as any).mock.calls[0][1] as FormData;
    expect(formData.get('file')).toBe(file);
    expect(result.id).toBe('job-2');
  });

  it('getImportJob hits the import job status endpoint', async () => {
    (api.get as any).mockResolvedValue({ data: { id: 'ij1', status: 'COMPLETED' } });
    const result = await getImportJob('ij1');
    expect(api.get).toHaveBeenCalledWith('/import/jobs/ij1');
    expect(result.status).toBe('COMPLETED');
  });
});
