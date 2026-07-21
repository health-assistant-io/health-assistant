import { describe, it, expect, vi, beforeEach } from 'vitest';
import api from '../api/axios';
import * as conceptService from './conceptService';
import {
  getLinkSchema,
  createLinksFor,
  selectionsToLinkInputs,
} from './conceptEdges';
import type { CatalogSelection } from '../types/catalog';

describe('conceptEdges service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.get = vi.fn();
    api.post = vi.fn();
  });

  describe('getLinkSchema', () => {
    it('fetches the full matrix when called with no args', async () => {
      const mockRows = [
        { src_type: 'medication', dst_type: 'concept', relations: ['TREATS'] },
      ];
      (api.get as any).mockResolvedValueOnce({ data: mockRows });

      const result = await getLinkSchema();

      expect(api.get).toHaveBeenCalledWith('/concept-edges/schema');
      expect(result).toEqual(mockRows);
    });

    it('passes src_type as a query param when given', async () => {
      const mockObj = { concept: ['TREATS', 'CONTRAINDICATES'] };
      (api.get as any).mockResolvedValueOnce({ data: mockObj });

      const result = await getLinkSchema('medication');

      expect(api.get).toHaveBeenCalledWith(
        '/concept-edges/schema?src_type=medication',
      );
      expect(result).toEqual(mockObj);
    });

    it('passes src_type + dst_type as query params when both given', async () => {
      const mockObj = { relations: ['TREATS'] };
      (api.get as any).mockResolvedValueOnce({ data: mockObj });

      const result = await getLinkSchema('medication', 'concept');

      expect(api.get).toHaveBeenCalledWith(
        '/concept-edges/schema?src_type=medication&dst_type=concept',
      );
      expect(result).toEqual(mockObj);
    });
  });

  describe('createLinksFor', () => {
    it('POSTs each link and collects per-link results (all-success)', async () => {
      const createEdgeSpy = vi
        .spyOn(conceptService, 'createEdge')
        .mockResolvedValueOnce({ id: 'edge-1' } as any)
        .mockResolvedValueOnce({ id: 'edge-2' } as any);

      const results = await createLinksFor('medication', 'med-1', [
        { dst_type: 'concept', dst_id: 'c1', relation: 'TREATS' },
        {
          dst_type: 'biomarker',
          dst_id: 'b1',
          relation: 'MONITORS',
          properties: { note: 'drug level' },
        },
      ]);

      expect(createEdgeSpy).toHaveBeenCalledTimes(2);
      expect(createEdgeSpy).toHaveBeenCalledWith({
        src_type: 'medication',
        src_id: 'med-1',
        dst_type: 'concept',
        dst_id: 'c1',
        relation: 'TREATS',
        properties: undefined,
        source: 'ai',
        status: 'approved',
        tenant_scoped: true,
      });
      expect(createEdgeSpy).toHaveBeenCalledWith({
        src_type: 'medication',
        src_id: 'med-1',
        dst_type: 'biomarker',
        dst_id: 'b1',
        relation: 'MONITORS',
        properties: { note: 'drug level' },
        source: 'ai',
        status: 'approved',
        tenant_scoped: true,
      });
      expect(results).toEqual([
        {
          ok: true,
          dst: { type: 'concept', id: 'c1', label: '' },
          relation: 'TREATS',
          edge_id: 'edge-1',
        },
        {
          ok: true,
          dst: { type: 'biomarker', id: 'b1', label: '' },
          relation: 'MONITORS',
          edge_id: 'edge-2',
        },
      ]);
    });

    it('continues the loop on per-link failure (best-effort)', async () => {
      vi.spyOn(conceptService, 'createEdge')
        .mockRejectedValueOnce(new Error('duplicate edge'))
        .mockResolvedValueOnce({ id: 'edge-2' } as any);

      const results = await createLinksFor('medication', 'med-1', [
        { dst_type: 'concept', dst_id: 'c1', relation: 'TREATS' },
        { dst_type: 'concept', dst_id: 'c2', relation: 'CONTRAINDICATES' },
      ]);

      expect(results).toHaveLength(2);
      expect(results[0].ok).toBe(false);
      expect(results[0].error).toBe('duplicate edge');
      expect(results[1].ok).toBe(true);
      expect(results[1].edge_id).toBe('edge-2');
    });

    it('passes through opts (tenant_scoped / source / status)', async () => {
      const createEdgeSpy = vi
        .spyOn(conceptService, 'createEdge')
        .mockResolvedValue({ id: 'x' } as any);

      await createLinksFor(
        'medication',
        'med-1',
        [{ dst_type: 'concept', dst_id: 'c1', relation: 'TREATS' }],
        { tenant_scoped: false, source: 'manual', status: 'proposed' },
      );

      expect(createEdgeSpy).toHaveBeenCalledWith({
        src_type: 'medication',
        src_id: 'med-1',
        dst_type: 'concept',
        dst_id: 'c1',
        relation: 'TREATS',
        properties: undefined,
        source: 'manual',
        status: 'proposed',
        tenant_scoped: false,
      });
    });

    it('returns an empty array when given an empty list', async () => {
      const createEdgeSpy = vi.spyOn(conceptService, 'createEdge');
      const results = await createLinksFor('medication', 'med-1', []);
      expect(createEdgeSpy).not.toHaveBeenCalled();
      expect(results).toEqual([]);
    });

    it('handles non-Error rejection payloads (string / unknown)', async () => {
      vi.spyOn(conceptService, 'createEdge')
        .mockRejectedValueOnce('string error')
        .mockRejectedValueOnce({ weird: true });

      const results = await createLinksFor('medication', 'med-1', [
        { dst_type: 'concept', dst_id: 'c1', relation: 'TREATS' },
        { dst_type: 'concept', dst_id: 'c2', relation: 'INDICATES' },
      ]);

      expect(results[0].ok).toBe(false);
      expect(results[0].error).toBe('string error');
      expect(results[1].ok).toBe(false);
      expect(results[1].error).toBe('unknown error');
    });
  });

  describe('selectionsToLinkInputs', () => {
    it('maps CatalogSelection[] to LinkInput[] preserving relation+label', () => {
      const selections: CatalogSelection[] = [
        { type: 'concept', id: 'c1', label: 'Diabetes', relation: 'TREATS' },
        { type: 'biomarker', id: 'b1', label: 'INR', relation: 'MONITORS' },
      ];

      expect(selectionsToLinkInputs(selections)).toEqual([
        { dst_type: 'concept', dst_id: 'c1', dst_label: 'Diabetes', relation: 'TREATS' },
        { dst_type: 'biomarker', dst_id: 'b1', dst_label: 'INR', relation: 'MONITORS' },
      ]);
    });

    it('drops selections without a relation', () => {
      const selections = [
        { type: 'concept', id: 'c1', label: 'X', relation: 'TREATS' },
        { type: 'concept', id: 'c2', label: 'Y' }, // no relation
      ] as CatalogSelection[];

      expect(selectionsToLinkInputs(selections)).toHaveLength(1);
    });
  });
});
