import { 
  Pill, 
  FileText, 
  ShieldAlert, 
  Activity, 
  User, 
  MapPin, 
  Calendar
} from 'lucide-react';
import { CalendarEvent, CalendarEventType } from '../types/calendar';
import { TFunction } from 'i18next';

/**
 * Maps a CalendarEventType to a Lucide icon component
 */
export const getEventIcon = (type: CalendarEventType) => {
  switch (type) {
    case 'medication': return Pill;
    case 'examination': return FileText;
    case 'allergy': return ShieldAlert;
    case 'clinical-event': return Activity;
    default: return Calendar;
  }
};

/**
 * Maps a CalendarEventType to a SummaryModal 'type' string
 */
export const getModalType = (type: CalendarEventType): any => {
  if (type === 'clinical-event') return 'event';
  return type;
};

/**
 * Generates navigation path for a clinical event
 */
export const getEventNavigationPath = (event: CalendarEvent): string | undefined => {
  const { type, originalData, id } = event;
  
  if (type === 'medication' && (originalData?.code?.catalog_id || originalData?.id)) {
      return `/medications/details/${originalData.code?.catalog_id || originalData.id}`;
  } else if (type === 'examination') {
      return `/examinations/${id}`;
  } else if (type === 'clinical-event') {
      return `/events/${originalData?.id || id.split('-')[0]}`;
  }
  return undefined;
};

/**
 * Standardizes CalendarEvent data into SummaryModal props
 */
export const getEventSummaryProps = (event: CalendarEvent, t: TFunction) => {
  const { type, originalData, subtitle, title, date, time } = event;

  const fields = [];
  let description = undefined;
  let alert = undefined;
  let tags: string[] = [];

  switch (type) {
    case 'medication':
      fields.push(
        { label: t('medications.dosage'), value: originalData?.dosage || subtitle, icon: Pill },
        { label: t('medications.route'), value: originalData?.route || 'Oral', icon: Activity },
        { label: t('medications.status'), value: originalData?.status || 'Active', color: 'text-green-500' }
      );
      description = originalData?.reason;
      tags = [originalData?.category].filter(Boolean);
      break;

    case 'allergy':
      fields.push(
        { label: t('allergies.modal.criticality'), value: originalData?.criticality, color: originalData?.criticality === 'high' ? 'text-red-500' : 'text-blue-500' },
        { label: t('allergies.modal.clinical_status'), value: originalData?.clinical_status }
      );
      if (originalData?.criticality === 'high') {
        alert = {
          message: t('allergies.modal.high_criticality_warning'),
          type: 'critical' as const
        };
      }
      break;

    case 'examination':
      fields.push(
        { label: t('examinations.doctor'), value: originalData?.doctor_name, icon: User },
        { label: t('organizations.address'), value: originalData?.location_name, icon: MapPin }
      );
      description = originalData?.notes;
      break;

    case 'clinical-event':
      fields.push(
        { label: t('common.category'), value: originalData?.type_details?.category?.name || 'General', icon: Activity },
        { label: t('documents_explorer.status'), value: originalData?.status || 'Active' }
      );
      description = originalData?.description;
      break;
  }

  return {
    title,
    subtitle,
    type: getModalType(type),
    icon: getEventIcon(type),
    date,
    time: (time && time !== 'Unspecified') ? time : undefined,
    fields: fields.filter(f => f.value),
    description,
    tags,
    alert,
    navigationPath: getEventNavigationPath(event)
  };
};
