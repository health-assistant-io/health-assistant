/**
 * useBiomarkerDetailData — loads one biomarker definition + its longitudinal
 * trend for a patient, with the time-range/aggregation state the trend chart
 * drives. The data counterpart of the BiomarkerDetail page's load logic,
 * factored out so the observation overlay can render the same rich tabs
 * (Trend / Clinical / History) without re-fetching by hand.
 *
 * Returns `biomarker: null` when the id doesn't resolve (e.g. an unmapped
 * observation with no definition) so the caller can fall back to a value-only
 * card.
 */
import { useEffect, useMemo, useState } from 'react';
import biomarkerService from '../services/biomarkerService';
import { getBiomarkerTrends } from '../services/analyticsService';
import type { Biomarker } from '../types/biomarker';
import {
  DEFAULT_AGGREGATIONS,
  getCutoffDate,
  type AggregationBucket,
  type TimePeriod,
} from '../config/timeRanges';

export interface BiomarkerDetailData {
  biomarker: Biomarker | null;
  trends: any[];
  filteredTrends: any[];
  loading: boolean;
  dateRange: TimePeriod;
  setDateRange: (r: TimePeriod) => void;
  aggregation: AggregationBucket | null;
  setAggregation: (a: AggregationBucket | null) => void;
}

export function useBiomarkerDetailData(
  biomarkerId: string | null | undefined,
  patientId?: string,
): BiomarkerDetailData {
  const [biomarker, setBiomarker] = useState<Biomarker | null>(null);
  const [trends, setTrends] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [dateRange, setDateRange] = useState<TimePeriod>('all-time');
  const [aggregation, setAggregation] = useState<AggregationBucket | null>(null);

  // Telemetry biomarkers aggregate (the raw stream is too dense to chart raw).
  useEffect(() => {
    if (biomarker?.is_telemetry && dateRange) {
      setAggregation(DEFAULT_AGGREGATIONS[dateRange] || '1 day');
    } else {
      setAggregation(null);
    }
  }, [dateRange, biomarker?.is_telemetry]);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      if (!biomarkerId) {
        setBiomarker(null);
        setTrends([]);
        setLoading(false);
        return;
      }
      setLoading(true);
      try {
        const bio = await biomarkerService.getBiomarkerById(biomarkerId).catch(() => null);
        if (cancelled) return;
        if (!bio) {
          setBiomarker(null);
          setTrends([]);
          return;
        }
        setBiomarker(bio);
        const trendsData = patientId
          ? await getBiomarkerTrends('', bio.slug, dateRange, patientId, aggregation || undefined)
          : { biomarkers: {} };
        if (cancelled) return;
        setTrends(trendsData.biomarkers?.[bio.slug] ?? []);
      } catch {
        if (!cancelled) {
          setBiomarker(null);
          setTrends([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [biomarkerId, patientId, dateRange, aggregation]);

  const filteredTrends = useMemo(() => {
    if (!trends.length) return [];
    if (dateRange === 'all-time') return trends;
    const cutoff = getCutoffDate(dateRange);
    return trends.filter((d) => new Date(d.date) >= cutoff);
  }, [trends, dateRange]);

  return {
    biomarker,
    trends,
    filteredTrends,
    loading,
    dateRange,
    setDateRange,
    aggregation,
    setAggregation,
  };
}
