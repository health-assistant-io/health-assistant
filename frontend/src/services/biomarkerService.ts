import { Biomarker, Unit, BiomarkerGroup, BiomarkerReferenceRange } from '../types/biomarker';
import api from '../api/axios';

const API_URL = '/biomarkers';

/** Fields that make up a range payload (excludes id/biomarker_id). */
const RANGE_FIELDS: (keyof BiomarkerReferenceRange)[] = [
  'sex',
  'age_min',
  'age_max',
  'unit_id',
  'low',
  'high',
  'text',
  'applies_to',
];

/** Build a POST/PUT payload from a draft range (drops id/biomarker_id). */
function stripRange(r: BiomarkerReferenceRange): Omit<BiomarkerReferenceRange, 'id' | 'biomarker_id'> {
  const out: Record<string, unknown> = {};
  for (const k of RANGE_FIELDS) out[k] = r[k] ?? null;
  return out as Omit<BiomarkerReferenceRange, 'id' | 'biomarker_id'>;
}

/** Shallow compare the comparable fields of two ranges. */
function rangesEqual(a: BiomarkerReferenceRange, b: BiomarkerReferenceRange): boolean {
  for (const k of RANGE_FIELDS) {
    const av = a[k] ?? null;
    const bv = b[k] ?? null;
    if (av !== bv) return false;
  }
  return true;
}

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

  async retryMigration(id: string): Promise<Biomarker> {
    return api.post<Biomarker>(`${API_URL}/${id}/retry-migration`).then(res => res.data);
  }

  async remapObservations(biomarkerId: string, sourceName: string, patientId?: string): Promise<{ status: string; observations_remapped: number }> {
    const response = await api.post(`${API_URL}/${biomarkerId}/remap`, {
      source_name: sourceName,
      patient_id: patientId || null,
    });
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

  // --- Stratified reference ranges (audit B9/F3) ---
  // Nested under the parent biomarker; access inherited from the parent.

  async listReferenceRanges(biomarkerId: string): Promise<BiomarkerReferenceRange[]> {
    const response = await api.get(`${API_URL}/${biomarkerId}/reference-ranges`);
    return response.data;
  }

  async createReferenceRange(
    biomarkerId: string,
    data: Omit<BiomarkerReferenceRange, 'id' | 'biomarker_id'>,
  ): Promise<BiomarkerReferenceRange> {
    const response = await api.post(`${API_URL}/${biomarkerId}/reference-ranges`, data);
    return response.data;
  }

  async updateReferenceRange(
    biomarkerId: string,
    rangeId: string,
    data: Partial<BiomarkerReferenceRange>,
  ): Promise<BiomarkerReferenceRange> {
    const response = await api.put(`${API_URL}/${biomarkerId}/reference-ranges/${rangeId}`, data);
    return response.data;
  }

  async deleteReferenceRange(biomarkerId: string, rangeId: string): Promise<void> {
    await api.delete(`${API_URL}/${biomarkerId}/reference-ranges/${rangeId}`);
  }

  /**
   * Reconcile a biomarker's stratified ranges against a desired list.
   *
   * Used by the catalog form's save flow so ranges can be edited in **draft
   * mode** (including on first create, before the biomarker has an id) and
   * committed in one diff after the biomarker is saved: POST new rows (no id),
   * PUT changed rows, DELETE removed rows, leave unchanged rows alone.
   */
  async syncReferenceRanges(
    biomarkerId: string,
    desired: BiomarkerReferenceRange[],
  ): Promise<void> {
    const current = await this.listReferenceRanges(biomarkerId);
    const currentIds = new Set(current.filter((r) => r.id).map((r) => r.id!));
    const desiredIds = new Set(desired.filter((r) => r.id).map((r) => r.id!));

    // Delete ranges that no longer exist in the draft.
    for (const c of current) {
      if (c.id && !desiredIds.has(c.id)) {
        await this.deleteReferenceRange(biomarkerId, c.id);
      }
    }
    // Create new + update changed.
    for (const d of desired) {
      if (!d.id || !currentIds.has(d.id)) {
        await this.createReferenceRange(biomarkerId, stripRange(d));
      } else {
        const prev = current.find((c) => c.id === d.id);
        if (prev && !rangesEqual(prev, d)) {
          await this.updateReferenceRange(biomarkerId, d.id, stripRange(d));
        }
      }
    }
  }
}

export default new BiomarkerService();
