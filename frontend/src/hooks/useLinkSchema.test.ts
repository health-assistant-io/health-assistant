import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { LinkSchemaRow } from '../services/conceptEdges';
import {
  useLinkSchema,
  deriveSchemaForSource,
  _resetLinkSchemaCacheForTests,
} from './useLinkSchema';

// Mock the service module so the hook's call to `conceptEdges.getLinkSchema`
// hits our test double instead of the network. (vitest's ESM exports are
// frozen, so `vi.spyOn` doesn't intercept the hook's call — `vi.mock` does.)
// `vi.hoisted` runs BEFORE the import, so the mock factory can safely
// reference the mock fn.
const mocks = vi.hoisted(() => ({
  getLinkSchema: vi.fn(),
}));
vi.mock('../services/conceptEdges', () => ({
  getLinkSchema: mocks.getLinkSchema,
}));

describe('useLinkSchema hook', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _resetLinkSchemaCacheForTests();
    mocks.getLinkSchema.mockReset();
  });

  it('fetches the full matrix on first mount', async () => {
    const mockRows: LinkSchemaRow[] = [
      { src_type: 'medication', dst_type: 'concept', relations: ['TREATS'] },
      { src_type: 'biomarker', dst_type: 'concept', relations: ['MEMBER_OF'] },
    ];
    mocks.getLinkSchema.mockResolvedValue(mockRows);

    const { result } = renderHook(() => useLinkSchema());

    expect(result.current.loading).toBe(true);
    await act(async () => {
      await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    });

    expect(mocks.getLinkSchema).toHaveBeenCalledTimes(1);
    expect(result.current.schema).toEqual(mockRows);
    expect(result.current.error).toBeNull();
  });

  it('projects to per-source view when srcType is given', async () => {
    const mockRows: LinkSchemaRow[] = [
      { src_type: 'medication', dst_type: 'concept', relations: ['TREATS'] },
      { src_type: 'medication', dst_type: 'biomarker', relations: ['MONITORS'] },
      { src_type: 'biomarker', dst_type: 'concept', relations: ['MEMBER_OF'] },
    ];
    mocks.getLinkSchema.mockResolvedValue(mockRows);

    const { result } = renderHook(() => useLinkSchema('medication'));
    await act(async () => {
      await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    });

    expect(result.current.schema).toEqual({
      concept: ['TREATS'],
      biomarker: ['MONITORS'],
    });
  });

  it('caches across mounts (single network call for multiple hooks)', async () => {
    const mockRows: LinkSchemaRow[] = [
      { src_type: 'medication', dst_type: 'concept', relations: ['TREATS'] },
    ];
    mocks.getLinkSchema.mockResolvedValue(mockRows);

    const { unmount: unmount1 } = renderHook(() => useLinkSchema());
    await act(async () => {
      await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    });
    unmount1();

    const { result } = renderHook(() => useLinkSchema());
    await act(async () => {
      await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    });

    // Still only one network call — the cache served the second mount.
    expect(mocks.getLinkSchema).toHaveBeenCalledTimes(1);
    expect(result.current.schema).toEqual(mockRows);
  });

  it('surfaces fetch errors and allows retry on next mount', async () => {
    mocks.getLinkSchema.mockRejectedValueOnce(new Error('network down'));

    const { result } = renderHook(() => useLinkSchema());
    await act(async () => {
      await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    });

    expect(result.current.error).toBe('network down');
    expect(result.current.schema).toBeNull();

    // Retry succeeds on a new mount (the failed promise was cleared).
    mocks.getLinkSchema.mockResolvedValueOnce([
      { src_type: 'medication', dst_type: 'concept', relations: ['TREATS'] },
    ]);
    const { result: result2 } = renderHook(() => useLinkSchema());
    await act(async () => {
      await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
    });
    expect(result2.current.error).toBeNull();
    expect(result2.current.schema).not.toBeNull();
  });
});

describe('deriveSchemaForSource', () => {
  it('returns a dst_type→relations map filtered by src_type', () => {
    const rows = [
      { src_type: 'medication', dst_type: 'concept', relations: ['TREATS'] },
      { src_type: 'medication', dst_type: 'biomarker', relations: ['MONITORS'] },
      { src_type: 'biomarker', dst_type: 'concept', relations: ['MEMBER_OF'] },
    ];
    expect(deriveSchemaForSource(rows, 'medication')).toEqual({
      concept: ['TREATS'],
      biomarker: ['MONITORS'],
    });
  });

  it('returns an empty object when src_type matches nothing', () => {
    const rows = [
      { src_type: 'medication', dst_type: 'concept', relations: ['TREATS'] },
    ];
    expect(deriveSchemaForSource(rows, 'biomarker')).toEqual({});
  });
});
