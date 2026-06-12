import React from 'react';
import { Activity } from 'lucide-react';

interface Props {
  className?: string;
  showText?: boolean;
}

export const AIBadge: React.FC<Props> = ({ className = '', showText = true }) => (
  <span 
    className={`flex items-center space-x-0.5 px-1.5 py-0.5 bg-blue-600 text-white text-[8px] font-black rounded-sm uppercase tracking-tighter shadow-sm ${className}`}
    title="AI Analysis"
  >
    <Activity className="w-2 h-2" />
    {showText && <span>AI</span>}
  </span>
);
