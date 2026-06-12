import api from '../api/axios';

export async function importCatalogFromUrl(url: string): Promise<{ message: string }> {
  const response = await api.post<{ message: string }>(`/admin/catalogs/import/url?url=${encodeURIComponent(url)}`);
  return response.data;
}

export async function importCatalogFromFile(file: File): Promise<{ message: string }> {
  const formData = new FormData();
  formData.append('file', file);
  
  const response = await api.post<{ message: string }>('/admin/catalogs/import/file', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return response.data;
}
