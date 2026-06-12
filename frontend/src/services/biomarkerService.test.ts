import { describe, it, expect, vi, beforeEach } from 'vitest';
import biomarkerService from './biomarkerService';
import api from '../api/axios';

describe('biomarkerService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    
    // Mock the axios instance
    api.get = vi.fn();
    api.post = vi.fn();
  });

  it('should fetch all biomarkers', async () => {
    const mockData = [
      { id: '1', slug: 'hdl', name: 'HDL', aliases: [] }
    ];
    (api.get as any).mockResolvedValueOnce({ data: mockData });

    const result = await biomarkerService.getAllBiomarkers();
    
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/biomarkers/'));
    expect(result).toEqual(mockData);
  });

  it('should fetch all units', async () => {
    const mockData = [
      { id: '1', symbol: 'mg/dL', name: 'Milligrams per deciliter', quantity_type: 'mass_concentration', conversion_multiplier: 1 }
    ];
    (api.get as any).mockResolvedValueOnce({ data: mockData });

    const result = await biomarkerService.getUnits();
    
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/biomarkers/units'));
    expect(result).toEqual(mockData);
  });

  it('should fetch all groups', async () => {
    const mockData = [
      { id: '1', name: 'Lipid Panel', members: [] }
    ];
    (api.get as any).mockResolvedValueOnce({ data: mockData });

    const result = await biomarkerService.getGroups();
    
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining('/biomarkers/groups'));
    expect(result).toEqual(mockData);
  });

  it('should create a new biomarker', async () => {
    const mockPayload = {
      slug: 'new-bio',
      name: 'New Bio',
      category: 'custom',
      aliases: ['nb']
    };
    const mockData = { id: '1', ...mockPayload };
    (api.post as any).mockResolvedValueOnce({ data: mockData });

    const result = await biomarkerService.createBiomarker(mockPayload);
    
    expect(api.post).toHaveBeenCalledWith(expect.stringContaining('/biomarkers/'), mockPayload);
    expect(result).toEqual(mockData);
  });
});
