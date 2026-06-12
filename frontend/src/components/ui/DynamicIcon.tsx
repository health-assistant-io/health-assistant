import React from 'react';
import * as LucideIcons from 'lucide-react';
import { HelpCircle } from 'lucide-react';

export interface IconConfig {
  type: 'lucide' | 'custom_svg';
  value: string;
}

interface DynamicIconProps {
  icon: string | IconConfig | null | undefined;
  className?: string;
  size?: number | string;
  color?: string;
}

export const DynamicIcon: React.FC<DynamicIconProps> = ({ 
  icon, 
  className = "w-5 h-5", 
  size,
  color 
}) => {
  if (!icon) {
    return <HelpCircle className={className} size={size} color={color} />;
  }

  // Handle legacy string icons
  if (typeof icon === 'string') {
    const LucideIcon = (LucideIcons as any)[icon] || HelpCircle;
    return <LucideIcon className={className} size={size} color={color} />;
  }

  // Handle structured icon config
  if (icon.type === 'custom_svg') {
    return (
      <div 
        className={`${className} flex items-center justify-center [&>svg]:w-full [&>svg]:h-full`}
        style={{ width: size, height: size, color: color }}
        dangerouslySetInnerHTML={{ __html: icon.value }} 
      />
    );
  }

  // Handle lucide type in structured config
  if (icon.type === 'lucide') {
    const LucideIcon = (LucideIcons as any)[icon.value] || HelpCircle;
    return <LucideIcon className={className} size={size} color={color} />;
  }

  return <HelpCircle className={className} size={size} color={color} />;
};
