import React from 'react';
import { X } from 'lucide-react';

export interface CardProps {
  id: string;
  isEditMode: boolean;
  onRemove?: (id: string) => void;
  children: React.ReactNode;
  style?: React.CSSProperties;
  className?: string;
  onMouseDown?: React.MouseEventHandler;
  onMouseUp?: React.MouseEventHandler;
  onTouchEnd?: React.TouchEventHandler;
  // Other props might be passed by react-grid-layout
  [key: string]: any;
}

export const CardWrapper = React.forwardRef<HTMLDivElement, CardProps>(({ 
  id, isEditMode, onRemove, children, style, className, onMouseDown, onMouseUp, onTouchEnd,
  // Filter out custom props that shouldn't go to the DOM
  availableBiomarkers, onUpdateConfig, config, data, 
  ...props 
}, ref) => (
  <div 
    ref={ref}
    style={style}
    className={`${className || ''} bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border p-3 sm:p-5 flex flex-col justify-between relative group ${isEditMode ? 'z-20 focus-within:z-[100]' : 'overflow-hidden'}`}
    onMouseDown={onMouseDown}
    onMouseUp={onMouseUp}
    onTouchEnd={onTouchEnd}
    {...props}
  >
    {isEditMode && onRemove && (
      <button 
        onClick={(e) => { e.stopPropagation(); onRemove(id); }}
        aria-label="Remove card"
        className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full p-1.5 shadow-lg opacity-0 group-hover:opacity-100 transition-opacity z-[60] hover:bg-red-600 active:scale-95"
      >
        <X className="w-3 h-3" />
      </button>
    )}
    {children}
  </div>
));
