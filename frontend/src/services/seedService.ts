import api from '../api/axios';

/**
 * Download the running instance's global taxonomy/anatomy/catalog as a ZIP of
 * seed-format JSON files (SYSTEM_ADMIN only). The maintainer transfers the ZIP
 * to their dev machine and unpacks it into backend/data/seeds/ (via
 * scripts/unpack_seeds_zip.py), then reviews with `git diff data/seeds/`.
 *
 * Read-only on the server — never writes data/seeds/.
 */
export async function downloadSeedsZip(filename = 'health-assistant-seeds.zip'): Promise<void> {
  const response = await api.get('/admin/seeds/export.zip', {
    responseType: 'blob',
  });
  const blob = response.data as Blob;
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return;
  }
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
}
