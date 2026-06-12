export const DOCUMENT_CATEGORIES = [
  { id: 'laboratory-tests', label: 'Laboratory Tests' },
  { id: 'imaging-radiology', label: 'Documents Explorer' },
  { id: 'vital-signs', label: 'Vital Signs' },
  { id: 'blood_laboratory', label: 'Blood Laboratory' },
  { id: 'urine_laboratory', label: 'Urine Laboratory' },
  { id: 'cardiology', label: 'Cardiology' },
  { id: 'neurology', label: 'Neurology' },
  { id: 'ophthalmology', label: 'Ophthalmology' },
  { id: 'gastroenterology', label: 'Gastroenterology' },
  { id: 'pulmonology', label: 'Pulmonology' },
  { id: 'dentistry', label: 'Dentistry' },
  { id: 'pathology', label: 'Pathology' },
  { id: 'audiology', label: 'Audiology' },
  { id: 'auto_generated', label: 'Unmapped Results' },
  { id: 'other', label: 'Other' },
];

export const CATEGORY_LABELS = DOCUMENT_CATEGORIES.map(c => c.label);

export const CATEGORY_MAPPING: Record<string, string> = DOCUMENT_CATEGORIES.reduce((acc, cat) => {
  acc[cat.id] = cat.label;
  return acc;
}, {} as Record<string, string>);
