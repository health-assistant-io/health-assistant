/**
 * EventDetail — the single-record detail view for a clinical-event instance,
 * shown in `InstanceCard`'s "open" overlay. Reuses the existing
 * {@link ClinicalEventCard} (read-only, default variant) — the same component
 * the events list / dashboard render — so a picked event displays identically
 * here and across the app. No new UI invented.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle } from 'lucide-react';
import { ClinicalEventCard } from '../../../components/events/ClinicalEventCard';
import { LoadingState } from '../../../components/ui/LoadingState';
import { getEvent, type ClinicalEvent } from '../../../services/clinicalEventService';
import type { InstanceDetailProps } from '../../../components/instances/detailViewRegistry';

export const EventDetail: React.FC<InstanceDetailProps> = ({ id }) => {
  const { t } = useTranslation();
  const [event, setEvent] = useState<ClinicalEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setLoading(true);
      setError(false);
      try {
        const evt = await getEvent(id);
        if (!cancelled) setEvent(evt);
      } catch {
        if (!cancelled) {
          setError(true);
          setEvent(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (loading) {
    return <LoadingState variant="section" />;
  }
  if (error || !event) {
    return (
      <div className="flex flex-col items-center justify-center py-10 text-center">
        <AlertTriangle className="w-8 h-8 text-amber-400 mb-2" />
        <p className="text-sm text-gray-500 dark:text-dark-muted">
          {t('instances.card_unavailable', 'Record unavailable')}
        </p>
      </div>
    );
  }

  return (
    <div className="p-6">
      <ClinicalEventCard event={event} readOnly />
    </div>
  );
};
