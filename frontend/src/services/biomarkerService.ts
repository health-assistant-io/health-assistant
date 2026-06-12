import { Biomarker, Unit, BiomarkerGroup } from '../types/biomarker';
import api from '../api/axios';

const API_URL = '/biomarkers';

class BiomarkerService {
  async getAllBiomarkers(): Promise<Biomarker[]> {
    const response = await api.get(`${API_URL}/`);
    return response.data;
  }

  async getBiomarkerBySlug(slug: string): Promise<Biomarker> {
    const response = await api.get(`${API_URL}/slug/${slug}`);
    return response.data;
  }

  async getBiomarkerById(id: string): Promise<Biomarker> {
    // If it's not a UUID, it might be a slug
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
    if (!uuidRegex.test(id)) {
      return this.getBiomarkerBySlug(id);
    }
    const response = await api.get(`${API_URL}/${id}`);
    return response.data;
  }

  async getUnits(): Promise<Unit[]> {
    const response = await api.get(`${API_URL}/units`);
    return response.data;
  }

  async createUnit(data: { symbol: string; name: string; quantity_type?: string }): Promise<Unit> {
    const response = await api.post(`${API_URL}/units`, data);
    return response.data;
  }

  async getGroups(): Promise<BiomarkerGroup[]> {
    const response = await api.get(`${API_URL}/groups`);
    return response.data;
  }

  async createBiomarker(data: any): Promise<Biomarker> {
    const response = await api.post(`${API_URL}/`, data);
    return response.data;
  }

  async updateBiomarker(id: string, data: any): Promise<Biomarker> {
    const response = await api.patch(`${API_URL}/${id}`, data);
    return response.data;
  }

  async deleteBiomarker(id: string): Promise<any> {
    const response = await api.delete(`${API_URL}/${id}`);
    return response.data;
  }

  async bulkDeleteBiomarkers(ids: string[]): Promise<any> {
    const response = await api.post(`${API_URL}/bulk-delete`, { biomarker_ids: ids });
    return response.data;
  }
}

export default new BiomarkerService();
