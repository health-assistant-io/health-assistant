import { describe, it, expect, vi, beforeEach } from 'vitest';
import { downloadSeedsZip } from './seedService';
import api from '../api/axios';

vi.mock('../api/axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('seedService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('downloadSeedsZip requests a blob from the admin seeds endpoint', async () => {
    const blob = new Blob(['PK'], { type: 'application/zip' });
    (api.get as any).mockResolvedValue({ data: blob });

    if (typeof window === 'undefined') {
      await downloadSeedsZip();
      expect(api.get).toHaveBeenCalledWith('/admin/seeds/export.zip', { responseType: 'blob' });
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

    await downloadSeedsZip('my-seeds.zip');

    expect(api.get).toHaveBeenCalledWith('/admin/seeds/export.zip', { responseType: 'blob' });
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake-url');
    appendSpy.mockRestore();
    removeSpy.mockRestore();
  });
});
