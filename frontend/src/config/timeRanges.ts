export type TimePeriod = 'last-24-hours' | 'last-7-days' | 'last-30-days' | 'last-90-days' | 'last-12-months' | 'all-time';

export type AggregationBucket = '1 minute' | '5 minutes' | '15 minutes' | '1 hour' | '1 day' | '1 week' | '1 month';

export interface TimeRangeConfig {
  id: TimePeriod;
  shortLabel: string;
  longLabel: string;
}

export const TIME_RANGES: TimeRangeConfig[] = [
  { id: 'last-24-hours', shortLabel: '1D', longLabel: 'Last 24 Hours' },
  { id: 'last-7-days', shortLabel: '7D', longLabel: 'Last 7 Days' },
  { id: 'last-30-days', shortLabel: '30D', longLabel: 'Last 30 Days' },
  { id: 'last-90-days', shortLabel: '3M', longLabel: 'Last 90 Days' },
  { id: 'last-12-months', shortLabel: '1Y', longLabel: 'Last 12 Months' },
  { id: 'all-time', shortLabel: 'ALL', longLabel: 'All Time' }
];

export const AGGREGATION_OPTIONS: { id: AggregationBucket; label: string }[] = [
  { id: '1 minute', label: '1 Min' },
  { id: '5 minutes', label: '5 Min' },
  { id: '15 minutes', label: '15 Min' },
  { id: '1 hour', label: '1 Hour' },
  { id: '1 day', label: '1 Day' },
  { id: '1 week', label: '1 Week' },
  { id: '1 month', label: '1 Month' },
];

export const DEFAULT_AGGREGATIONS: Record<TimePeriod, AggregationBucket> = {
  'last-24-hours': '15 minutes',
  'last-7-days': '1 hour',
  'last-30-days': '1 day',
  'last-90-days': '1 day',
  'last-12-months': '1 week',
  'all-time': '1 month'
};

export const getCutoffDate = (period: TimePeriod): Date => {
  const now = new Date();
  const cutoff = new Date();
  
  switch (period) {
    case 'last-24-hours': cutoff.setDate(now.getDate() - 1); break;
    case 'last-7-days': cutoff.setDate(now.getDate() - 7); break;
    case 'last-30-days': cutoff.setDate(now.getDate() - 30); break;
    case 'last-90-days': cutoff.setDate(now.getDate() - 90); break;
    case 'last-12-months': cutoff.setFullYear(now.getFullYear() - 1); break;
    case 'all-time': cutoff.setFullYear(now.getFullYear() - 10); break;
  }
  return cutoff;
};
