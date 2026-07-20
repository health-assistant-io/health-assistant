import React, { useState, useRef, useEffect, useMemo } from 'react';
import { 
  format, 
  isValid, 
  isSameDay, 
  startOfMonth, 
  endOfMonth, 
  startOfWeek, 
  endOfWeek, 
  addDays, 
  isSameMonth,
  addMonths,
  subMonths,
  setYear,
  setMonth,
  addYears,
  subYears,
  startOfDay,
  parseISO
} from 'date-fns';
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight, ChevronDown, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Popover } from './Popover';

type ViewMode = 'days' | 'months' | 'years';

interface DatePickerProps {
  value: string | null | undefined;
  onChange: (date: string) => void;
  className?: string;
  placeholder?: string;
  minDate?: Date;
  maxDate?: Date;
  required?: boolean;
  disabled?: boolean;
  id?: string;
  variant?: 'default' | 'unstyled';
  /**
   * Phase 8c: when true, render a clear (X) button inside the trigger when a
   * date is populated. Clicking it calls `onChange('')` (empty string — the
   * form's sentinel for "unset") and `onClear?.()`, without opening the
   * calendar popover. Defaults to `false` to preserve existing behavior on
   * callers that haven't opted in.
   */
  allowClear?: boolean;
  /** Optional callback fired when the user clicks the clear button. */
  onClear?: () => void;
}

export const DatePicker: React.FC<DatePickerProps> = ({
  value,
  onChange,
  className = '',
  placeholder,
  minDate,
  maxDate,
  required,
  disabled,
  id,
  variant = 'default',
  allowClear = false,
  onClear,
}) => {
  const { t } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('days');
  const [currentDate, setCurrentDate] = useState<Date>(new Date());
  const [yearPage, setYearPage] = useState<number>(new Date().getFullYear());
  
  const triggerRef = useRef<HTMLDivElement>(null);

  // Parse value to Date
  const selectedDate = useMemo(() => {
    if (!value) return null;
    const parsed = parseISO(value); // Assuming YYYY-MM-DD
    return isValid(parsed) ? parsed : null;
  }, [value]);

  // Sync currentDate with selectedDate when opened
  useEffect(() => {
    if (isOpen) {
      if (selectedDate) {
        setCurrentDate(selectedDate);
        setYearPage(selectedDate.getFullYear());
      } else {
        const today = new Date();
        setCurrentDate(today);
        setYearPage(today.getFullYear());
      }
      setViewMode('days');
    }
  }, [isOpen, selectedDate]);

  const handleSelectDate = (date: Date) => {
    if ((minDate && date < startOfDay(minDate)) || (maxDate && date > startOfDay(maxDate))) {
      return;
    }
    onChange(format(date, 'yyyy-MM-dd'));
    setIsOpen(false);
  };

  // Phase 8c: clear the field. Emits the empty-string sentinel (the form's
  // unset marker — already handled by submit-time coercion) and the optional
  // onClear callback, then closes the popover if it was open. Does NOT
  // propagate to the trigger's onClick (which would toggle the popover).
  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    onChange('');
    onClear?.();
    setIsOpen(false);
  };

  // Rendering Days
  const renderDays = () => {
    const monthStart = startOfMonth(currentDate);
    const monthEnd = endOfMonth(monthStart);
    const startDate = startOfWeek(monthStart, { weekStartsOn: 1 }); // 1 = Monday
    const endDate = endOfWeek(monthEnd, { weekStartsOn: 1 });

    const dateFormat = 'd';
    const rows = [];
    let days = [];
    let day = startDate;

    // Week days header
    const weekDays = [];
    for (let i = 0; i < 7; i++) {
      weekDays.push(
        <div key={i} className="text-center font-medium text-xs text-gray-500 py-1">
          {format(addDays(startOfWeek(new Date(), { weekStartsOn: 1 }), i), 'EEEEE')}
        </div>
      );
    }

    while (day <= endDate) {
      for (let i = 0; i < 7; i++) {
        const formattedDate = format(day, dateFormat);
        const cloneDay = day;
        const isSelected = selectedDate && isSameDay(day, selectedDate);
        const isCurrentMonth = isSameMonth(day, monthStart);
        const isToday = isSameDay(day, new Date());
        
        let isDisabled = false;
        if (minDate && cloneDay < startOfDay(minDate)) isDisabled = true;
        if (maxDate && cloneDay > startOfDay(maxDate)) isDisabled = true;

        days.push(
          <button
            key={day.toString()}
            type="button"
            disabled={isDisabled}
            onClick={() => handleSelectDate(cloneDay)}
            className={`
              w-8 h-8 flex items-center justify-center rounded-full text-sm transition-colors
              ${!isCurrentMonth ? 'text-gray-300 dark:text-gray-600' : 'text-gray-700 dark:text-gray-300'}
              ${isSelected ? 'bg-blue-600 text-white font-medium hover:bg-blue-700' : ''}
              ${!isSelected && isToday ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 font-medium' : ''}
              ${!isSelected && !isDisabled ? 'hover:bg-gray-100 dark:hover:bg-dark-hover' : ''}
              ${isDisabled ? 'opacity-30 cursor-not-allowed' : 'cursor-pointer'}
            `}
          >
            {formattedDate}
          </button>
        );
        day = addDays(day, 1);
      }
      rows.push(
        <div className="grid grid-cols-7 gap-1 mb-1" key={day.toString()}>
          {days}
        </div>
      );
      days = [];
    }

    return (
      <div className="px-2 pb-2">
        <div className="grid grid-cols-7 gap-1 mb-2">
          {weekDays}
        </div>
        {rows}
      </div>
    );
  };

  // Rendering Months
  const renderMonths = () => {
    const months = [];
    for (let i = 0; i < 12; i++) {
      const monthDate = setMonth(new Date(), i);
      const isSelected = selectedDate && selectedDate.getMonth() === i && selectedDate.getFullYear() === currentDate.getFullYear();
      
      months.push(
        <button
          key={i}
          type="button"
          onClick={() => {
            setCurrentDate(setMonth(currentDate, i));
            setViewMode('days');
          }}
          className={`
            py-3 rounded-xl text-sm font-medium transition-colors
            ${isSelected ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-dark-hover'}
          `}
        >
          {format(monthDate, 'MMM')}
        </button>
      );
    }

    return (
      <div className="grid grid-cols-3 gap-2 p-2">
        {months}
      </div>
    );
  };

  // Rendering Years
  const renderYears = () => {
    const years = [];
    // Show 12 years per page
    const startYear = Math.floor(yearPage / 12) * 12;
    
    for (let i = 0; i < 12; i++) {
      const year = startYear + i;
      const isSelected = selectedDate && selectedDate.getFullYear() === year;
      
      years.push(
        <button
          key={year}
          type="button"
          onClick={() => {
            setCurrentDate(setYear(currentDate, year));
            setViewMode('months');
          }}
          className={`
            py-3 rounded-xl text-sm font-medium transition-colors
            ${isSelected ? 'bg-blue-600 text-white' : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-dark-hover'}
          `}
        >
          {year}
        </button>
      );
    }

    return (
      <div className="grid grid-cols-3 gap-2 p-2">
        {years}
      </div>
    );
  };

  const getHeaderTitle = () => {
    if (viewMode === 'days') return format(currentDate, 'MMMM yyyy');
    if (viewMode === 'months') return format(currentDate, 'yyyy');
    if (viewMode === 'years') {
      const startYear = Math.floor(yearPage / 12) * 12;
      return `${startYear} - ${startYear + 11}`;
    }
    return '';
  };

  const handlePrev = () => {
    if (viewMode === 'days') setCurrentDate(subMonths(currentDate, 1));
    if (viewMode === 'months') setCurrentDate(subYears(currentDate, 1));
    if (viewMode === 'years') setYearPage(yearPage - 12);
  };

  const handleNext = () => {
    if (viewMode === 'days') setCurrentDate(addMonths(currentDate, 1));
    if (viewMode === 'months') setCurrentDate(addYears(currentDate, 1));
    if (viewMode === 'years') setYearPage(yearPage + 12);
  };

  const toggleViewMode = () => {
    if (viewMode === 'days') setViewMode('years');
    else if (viewMode === 'months') setViewMode('years');
  };

  return (
    <div className={`relative ${className.includes('w-') ? '' : 'w-full'}`} ref={triggerRef}>
      <div
        onClick={() => !disabled && setIsOpen(!isOpen)}
        className={`
          flex items-center transition-all text-left cursor-pointer
          ${className.includes('w-') ? '' : 'w-full'}
          ${variant === 'default' ? 'px-4 py-2.5 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border rounded-xl focus-within:ring-2 focus-within:ring-blue-500 hover:border-blue-300 dark:hover:border-blue-700' : ''}
          ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
          ${className}
        `}
      >
        {variant === 'default' && <CalendarIcon className="w-5 h-5 text-gray-400 dark:text-gray-500 mr-2 flex-shrink-0" />}
        <span className={`flex-1 ${!selectedDate ? (variant === 'default' ? 'text-gray-400' : 'opacity-70') : (variant === 'default' ? 'text-gray-900 dark:text-dark-text' : '')} overflow-hidden text-ellipsis whitespace-nowrap`}>
          {selectedDate ? format(selectedDate, 'dd/MM/yyyy') : placeholder || t('common.select_date', 'Select date')}
        </span>
        {allowClear && selectedDate && !disabled && (
          <button
            type="button"
            onClick={handleClear}
            aria-label={t('common.clear', 'Clear')}
            className={`flex-shrink-0 ml-1 p-0.5 rounded-full text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-200 dark:hover:bg-dark-hover transition-colors ${variant === 'default' ? '' : 'opacity-60 hover:opacity-100'}`}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      <input 
        type="hidden" 
        value={value || ''} 
        id={id}
        name={id}
        required={required}
      />

      <Popover
        isOpen={isOpen && !disabled}
        onClose={() => setIsOpen(false)}
        triggerRef={triggerRef}
        side="bottom"
        align="start"
        sideOffset={4}
        className="w-[280px]"
      >
        <div className="p-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-2xl shadow-xl">
          {/* Header */}
          <div className="flex items-center justify-between mb-3 px-1 pt-1">
            <button 
              type="button"
              onClick={handlePrev}
              className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-dark-hover transition-colors"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
            
            <button 
              type="button"
              onClick={toggleViewMode}
              className="px-3 py-1.5 rounded-lg text-sm font-semibold text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-dark-hover transition-colors flex items-center space-x-1"
            >
              <span>{getHeaderTitle()}</span>
              {viewMode !== 'years' && <ChevronDown className="w-4 h-4 ml-1" />}
            </button>
            
            <button 
              type="button"
              onClick={handleNext}
              className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-dark-hover transition-colors"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
          </div>

          {/* Calendar Body */}
          {viewMode === 'days' && renderDays()}
          {viewMode === 'months' && renderMonths()}
          {viewMode === 'years' && renderYears()}
        </div>
      </Popover>
    </div>
  );
};
