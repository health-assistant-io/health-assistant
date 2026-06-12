import React from 'react';
import { Activity } from 'lucide-react';

interface LoadingStateProps {
  message?: string;
  className?: string;
  variant?: 'fullscreen' | 'section' | 'inline' | 'mini';
  showText?: boolean;
}

/**
 * Reusable Loading State component for consistent UX across the application.
 * Features a modern, text-less option and focused partial loading.
 */
export const LoadingState: React.FC<LoadingStateProps> = ({ 
  message = 'Retrieving medical records...', 
  className = '',
  variant = 'section',
  showText = true
}) => {
  const isMini = variant === 'mini';
  const isInline = variant === 'inline';

  const containerClasses = {
    fullscreen: 'fixed inset-0 z-[150] flex flex-col items-center justify-center bg-white/80 dark:bg-dark-bg/80 backdrop-blur-sm',
    section: 'flex flex-col items-center justify-center py-40 w-full',
    inline: 'flex items-center space-x-2 py-2',
    mini: 'flex items-center justify-center p-4',
  }[variant];

  const iconSize = isMini || isInline ? 'w-5 h-5' : 'w-10 h-10';

  return (
    <div className={`${containerClasses} ${className} animate-in fade-in duration-300`}>
      <div className="relative flex items-center justify-center">
        {/* Subtle Background Glow */}
        <div className={`absolute ${iconSize} bg-blue-500/10 rounded-full blur-xl animate-pulse`} />
        
        {/* Discrete Progress Ring */}
        <svg className={`${variant === 'section' ? 'w-14 h-14' : 'w-7 h-7'} absolute animate-spin-slow opacity-20`} viewBox="0 0 100 100">
          <circle 
            cx="50" cy="50" r="45" 
            fill="none" 
            stroke="currentColor" 
            strokeWidth="2" 
            className="text-blue-600 dark:text-blue-400"
            strokeDasharray="70 200"
            strokeLinecap="round"
          />
        </svg>

        {/* Pulsing Core Icon */}
        <Activity className={`${iconSize} text-blue-600/80 dark:text-blue-400/80 animate-pulse stroke-[1.5px] relative z-10`} />
      </div>
      
      {showText && message && !isMini && (
        <div className="flex flex-col items-center mt-4 space-y-1">
          <p className={`text-gray-400 dark:text-dark-muted font-medium tracking-widest uppercase text-center ${isInline ? 'text-[7px]' : 'text-[9px]'}`}>
            {message}
          </p>
          {/* Discrete Dot Animation for status */}
          <div className="flex space-x-1">
            <div className="w-1 h-1 bg-blue-400 rounded-full animate-bounce [animation-delay:-0.3s]"></div>
            <div className="w-1 h-1 bg-blue-400 rounded-full animate-bounce [animation-delay:-0.15s]"></div>
            <div className="w-1 h-1 bg-blue-400 rounded-full animate-bounce"></div>
          </div>
        </div>
      )}
    </div>
  );
};
