import { 
  Activity, 
  Heart, 
  Thermometer, 
  Droplets, 
  Image as ImageIcon, 
  FileText, 
  FlaskConical, 
  Calendar as CalendarIcon,
  Pill
} from 'lucide-react';

export const IconMap: Record<string, any> = {
  Activity: Activity,
  Heart: Heart,
  Thermometer: Thermometer,
  Droplets: Droplets,
  ImageIcon: ImageIcon,
  FileText: FileText,
  FlaskConical: FlaskConical,
  Calendar: CalendarIcon,
  Pill: Pill
};

const BIOMARKER_ICON_MAP: Record<string, string> = {
  'heart rate': 'Heart',
  'pulse': 'Heart',
  'blood pressure': 'Activity',
  'temperature': 'Thermometer',
  'glucose': 'Droplets',
  'blood sugar': 'Droplets',
  'imaging': 'ImageIcon',
  'x-ray': 'ImageIcon',
  'mri': 'ImageIcon',
  'lab': 'FlaskConical',
  'cholesterol': 'Activity',
  'hemoglobin': 'Activity',
};

export const getBestIcon = (biomarker: any) => {
  if (!biomarker) return 'Activity';
  const name = typeof biomarker === 'object' ? (biomarker.name || biomarker.slug || '') : String(biomarker);
  const lower = name.toLowerCase();
  for (const [key, icon] of Object.entries(BIOMARKER_ICON_MAP)) {
    if (lower.includes(key)) return icon;
  }
  return 'Activity';
};
