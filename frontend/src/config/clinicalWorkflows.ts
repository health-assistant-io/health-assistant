import { Sparkles, ClipboardList, TrendingUp, ShieldAlert, FileText, Pill } from 'lucide-react';

export interface ClinicalAction {
  id: string;
  label: string;
  description: string;
  icon: any;
  prompt: string;
  color: string;
  category: 'documentation' | 'analysis' | 'safety';
}

export const CLINICAL_WORKFLOWS: ClinicalAction[] = [
  {
    id: 'health-summary',
    label: "Patient Health Summary",
    description: "Generate a comprehensive overview of the current health status",
    icon: ClipboardList,
    prompt: "Generate a professional clinical summary for this patient. Include a high-level overview of their health status, active medications, most recent critical biomarker findings, and any notable trends from their history.",
    color: "bg-blue-600",
    category: 'documentation'
  },
  {
    id: 'soap-note',
    label: "Draft SOAP Note",
    description: "Structure this session into standard medical documentation",
    icon: FileText,
    prompt: "Based on our conversation and the available clinical data, draft a structured SOAP note (Subjective, Objective, Assessment, Plan). Ensure the assessment section synthesizes current biomarker data if available.",
    color: "bg-indigo-600",
    category: 'documentation'
  },
  {
    id: 'trend-analysis',
    label: "Longitudinal Trend Analysis",
    description: "Deep dive into biomarker changes over the last 6 months",
    icon: TrendingUp,
    prompt: "Analyze the longitudinal trends for all available biomarkers over the last 6 months. Identify any concerning patterns, improvements, or results that require immediate clinical follow-up.",
    color: "bg-emerald-600",
    category: 'analysis'
  },
  {
    id: 'safety-audit',
    label: "Medication Safety Audit",
    description: "Review current medications for interactions and risks",
    icon: ShieldAlert,
    prompt: "Perform a thorough clinical audit of the patient's current medication list. Look for potential drug-drug interactions, contraindications based on their health profile, and suggest any necessary monitoring (like lab tests) for these specific drugs.",
    color: "bg-amber-600",
    category: 'safety'
  },
  {
    id: 'patient-education',
    label: "Explain to Patient",
    description: "Translate complex findings into patient-friendly language",
    icon: Sparkles,
    prompt: "Summarize the latest examination findings and biomarker results in clear, patient-friendly language. Avoid excessive medical jargon, explain why specific tests were performed, and suggest 3 simple questions the patient should ask their doctor during the next visit.",
    color: "bg-purple-600",
    category: 'documentation'
  }
];
