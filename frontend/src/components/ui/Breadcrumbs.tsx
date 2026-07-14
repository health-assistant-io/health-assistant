import React from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, Home } from 'lucide-react';
import { clsx } from 'clsx';

export interface BreadcrumbItem {
  label: string;
  path?: string;
  icon?: React.ReactNode;
}

interface BreadcrumbsProps {
  items?: BreadcrumbItem[];
  currentLabel?: string;
  className?: string;
}

export const Breadcrumbs: React.FC<BreadcrumbsProps> = ({ items = [], currentLabel, className }) => {
  if (items.length === 0 && !currentLabel) return null;

  return (
    <nav className={clsx("flex flex-wrap items-center gap-x-1.5 gap-y-0.5 mb-1", className)} aria-label="Breadcrumb">
      <Link 
        to="/" 
        className="text-gray-400 hover:text-blue-500 transition-colors p-0.5 shrink-0"
        title="Home"
      >
        <Home className="w-3 h-3" />
      </Link>
      
      {items.map((item, index) => (
        <React.Fragment key={index}>
          <ChevronRight className="w-2.5 h-2.5 text-gray-300 dark:text-dark-muted shrink-0" />
          {item.path ? (
            <Link
              to={item.path}
              className="text-[10px] font-bold text-gray-400 hover:text-blue-500 transition-colors uppercase tracking-wider whitespace-nowrap"
            >
              {item.label}
            </Link>
          ) : (
            <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider whitespace-nowrap">
              {item.label}
            </span>
          )}
        </React.Fragment>
      ))}

      {currentLabel && (
        <>
          <ChevronRight className="w-2.5 h-2.5 text-gray-300 dark:text-dark-muted shrink-0" />
          <span className="text-[10px] font-black text-brand-navy dark:text-dark-text uppercase tracking-wider min-w-0 break-words">
            {currentLabel}
          </span>
        </>
      )}
    </nav>
  );
};

export default Breadcrumbs;
