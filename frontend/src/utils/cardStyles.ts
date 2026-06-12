/**
 * Shared Tailwind classes for consistent card styling across the application.
 */
export const CardStyles = {
  // Main container for cards in a list
  container: (isSelected: boolean, isSelectable: boolean = false) => `
    relative group border transition-all duration-200 cursor-pointer overflow-hidden rounded-2xl
    ${isSelected 
      ? 'bg-blue-50/50 dark:bg-blue-900/10 shadow-md border-blue-200 dark:border-blue-900 ring-1 ring-blue-500/30 z-10' 
      : 'bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border hover:border-blue-100 hover:shadow-sm'
    }
    ${isSelectable ? 'pl-2' : ''}
  `,
  
  // Compact variant for sidebar/smaller contexts
  compact: (isSelected: boolean) => `
    group relative bg-white dark:bg-dark-surface p-4 rounded-2xl border transition-all cursor-pointer hover:shadow-sm
    ${isSelected 
      ? 'bg-blue-50/50 dark:bg-blue-900/10 border-blue-500 shadow-md ring-1 ring-blue-500/20' 
      : 'border-gray-100 dark:border-dark-border hover:border-blue-200'
    }
  `,

  // Inner padding for standard list cards
  inner: "p-5",
  
  // Header section (Date, Badge, etc.)
  header: "flex items-center justify-between mb-3",
  
  // Date text style
  date: (isSelected: boolean) => `text-xs font-bold ${isSelected ? 'text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-dark-muted'}`,
  
  // Title text style
  title: (isSelected: boolean) => `font-bold text-sm ${isSelected ? 'text-gray-900 dark:text-dark-text' : 'text-gray-700 dark:text-gray-200'}`,
  
  // Subtitle/Description text style
  description: "text-[11px] text-gray-400 italic mt-1"
};
