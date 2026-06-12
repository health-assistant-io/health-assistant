import { describe, it, expect, vi, beforeEach } from 'vitest';
import { uploadDocument, updateDocument } from './documentService';
import api from '../api/axios';

vi.mock('../api/axios', () => ({
  default: {
    post: vi.fn(),
    patch: vi.fn(),
    get: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('documentService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uploadDocument should include include_in_extraction in FormData', async () => {
    const file = new File(['test'], 'test.pdf', { type: 'application/pdf' });
    const mockResponse = { data: { id: '123', filename: 'test.pdf' } };
    (api.post as any).mockResolvedValue(mockResponse);

    await uploadDocument(file, 'patient-1', 'exam-1', true);

    expect(api.post).toHaveBeenCalledWith(
      '/documents',
      expect.any(FormData),
      expect.objectContaining({
        headers: { 'Content-Type': 'multipart/form-data' },
      })
    );

    const formData = (api.post as any).mock.calls[0][1] as FormData;
    expect(formData.get('include_in_extraction')).toBe('true');
    expect(formData.get('patient_id')).toBe('patient-1');
    expect(formData.get('examination_id')).toBe('exam-1');
  });

  it('updateDocument should send update payload', async () => {
    const mockResponse = { data: { id: '123', include_in_extraction: true } };
    (api.patch as any).mockResolvedValue(mockResponse);

    const result = await updateDocument('123', { include_in_extraction: true });

    expect(api.patch).toHaveBeenCalledWith('/documents/123', {
      include_in_extraction: true,
    });
    expect(result).toEqual(mockResponse.data);
  });
});
