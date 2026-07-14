import React, { useState, useMemo, useRef, useEffect } from 'react';
import { 
  format, 
  startOfWeek, 
  addDays, 
  startOfMonth, 
  endOfMonth, 
  endOfWeek, 
  isSameMonth, 
  isSameDay, 
  addMonths, 
  subMonths,
  eachDayOfInterval,
  startOfDay
} from 'date-fns';
import { useTranslation } from 'react-i18next';
import { 
  ChevronLeft, 
  ChevronRight, 
  Pill, 
  Clock, 
  Calendar as CalendarIcon,
  FileText,
  ShieldAlert,
  Info,
  ExternalLink,
  Search,
  Filter,
  Check,
  Grid,
  List,
  TrendingUp,
  X,
  ChevronDown,
  Layout,
  Activity
} from 'lucide-react';
import { CalendarEvent, CalendarConfig, CalendarEventType } from '../../types/calendar';
import { useCalendarData } from '../../hooks/useCalendarData';
import { Portal } from './Portal';
import { useNavigate } from 'react-router-dom';

type ViewType = 'timeline' | 'classic' | 'list' | 'history';

interface Props {
  /** Static events to display */
  events?: CalendarEvent[];
  /** Configuration for dynamic loading (takes precedence if patientId is provided) */
  config?: CalendarConfig;
  /** Custom click handler */
  onEventClick?: (event: CalendarEvent) => void;
  /** Title for the calendar */
  title?: string;
  /** Subtitle for the calendar */
  subtitle?: string;
  /** Compact mode for dashboard */
  compact?: boolean;
  /** Default view type */
  defaultView?: ViewType;
  /** Custom modal renderer */
  renderModal?: (event: CalendarEvent, onClose: () => void) => React.ReactNode;
  /** Hide the header section */
  hideHeader?: boolean;
  /** Remove background and borders (for use inside cards) */
  transparent?: boolean;
  /** In classic view, fit the entire month to the container height instead of scrolling */
  fitToContainer?: boolean;
  /** When provided, the calendar title becomes clickable and navigates to this in-app route */
  titleTo?: string;
}

export const UniversalCalendar: React.FC<Props> = ({ 
  events: staticEvents, 
  config, 
  onEventClick,
  title,
  compact = false,
  defaultView = 'classic',
  renderModal,
  hideHeader = false,
  transparent = false,
  subtitle,
  fitToContainer = false,
  titleTo
}) => {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  
  // State
  const [viewType, setViewType] = useState<ViewType>(defaultView);
  const [currentDate, setCurrentDate] = useState(new Date());
  const [selectedDate, setSelectedDate] = useState(new Date());
  const [searchTerm, setSearchTerm] = useState('');
  const [isViewDropdownOpen, setIsViewDropdownOpen] = useState(false);
  const [isFilterDropdownOpen, setIsFilterDropdownOpen] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<CalendarEvent | null>(null);
  const [isDraggingTimeline, setIsDraggingTimeline] = useState(false);
  const [examCategories, setExamCategories] = useState<any[]>([]);
  const [selectedExamCategories, setSelectedExamCategories] = useState<string[]>([]);
  const [selectedCategories, setSelectedCategories] = useState<CalendarEventType[]>(['medication', 'examination', 'allergy', 'clinical-event']);
  
  // Sync with props
  useEffect(() => {
    if (defaultView !== viewType) {
      setViewType(defaultView);
    }
  }, [defaultView]);
  const viewDropdownRef = useRef<HTMLDivElement>(null);
  const filterDropdownRef = useRef<HTMLDivElement>(null);
  const timelineRef = useRef<HTMLDivElement>(null);
  const dragInfo = useRef({ isDragging: false, startX: 0, scrollLeft: 0, hasMoved: false });

  // Fetch categories
  useEffect(() => {
    import('../../services/examinationService').then(m => {
        m.getExaminationCategories().then(cats => setExamCategories(cats));
    });
  }, []);

  // Dragging handlers for timeline
  const handleTimelineMouseDown = (e: React.MouseEvent) => {
    if (!timelineRef.current) return;
    dragInfo.current = {
      isDragging: true,
      startX: e.pageX - timelineRef.current.offsetLeft,
      scrollLeft: timelineRef.current.scrollLeft,
      hasMoved: false
    };
    setIsDraggingTimeline(true);
  };

  const handleTimelineMouseMove = (e: React.MouseEvent) => {
    if (!dragInfo.current.isDragging || !timelineRef.current) return;
    
    const x = e.pageX - timelineRef.current.offsetLeft;
    const walk = (x - dragInfo.current.startX) * 1.5;
    
    if (Math.abs(x - dragInfo.current.startX) > 5) {
      dragInfo.current.hasMoved = true;
    }
    
    timelineRef.current.scrollLeft = dragInfo.current.scrollLeft - walk;
  };

  const handleTimelineMouseUp = () => {
    dragInfo.current.isDragging = false;
    setIsDraggingTimeline(false);
    setTimeout(() => {
      dragInfo.current.hasMoved = false;
    }, 50);
  };

  // Close dropdowns on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (viewDropdownRef.current && !viewDropdownRef.current.contains(event.target as Node)) {
        setIsViewDropdownOpen(false);
      }
      if (filterDropdownRef.current && !filterDropdownRef.current.contains(event.target as Node)) {
        setIsFilterDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Date range calculations
  const { startDate, endDate, monthStart } = useMemo(() => {
    const startM = startOfMonth(currentDate);
    const endM = endOfMonth(startM);
    
    if (viewType === 'timeline') {
        const start = startOfDay(currentDate);
        return {
            monthStart: startM,
            startDate: start,
            endDate: addDays(start, 14)
        };
    }
    
    return {
      monthStart: startM,
      startDate: startOfWeek(startM),
      endDate: endOfWeek(endM)
    };
  }, [currentDate, viewType]);

  const internalConfig = useMemo(() => ({
    ...config,
    types: selectedCategories,
    examinationCategories: selectedExamCategories.length > 0 ? selectedExamCategories : undefined,
    startDate,
    endDate
  }), [config, selectedCategories, selectedExamCategories, startDate, endDate]);

  const { events: dynamicEvents, loading } = useCalendarData(internalConfig);

  const displayEvents = useMemo(() => {
    let source = config?.patientId ? dynamicEvents : (staticEvents || []);
    
    return source.filter(event => {
        const matchesSearch = searchTerm === '' || 
            event.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
            event.subtitle?.toLowerCase().includes(searchTerm.toLowerCase());
        
        const matchesCategory = selectedCategories.includes(event.type);
        
        if (event.type === 'examination' && selectedExamCategories.length > 0) {
            if (!selectedExamCategories.includes(event.category || '')) return false;
        }
        
        const matchesRange = viewType === 'classic' ? true : (event.date >= startDate && event.date <= endDate);
        
        return matchesSearch && matchesCategory && matchesRange;
    });
  }, [config?.patientId, dynamicEvents, staticEvents, searchTerm, selectedCategories, selectedExamCategories, viewType, startDate, endDate]);

  const toggleCategory = (cat: CalendarEventType) => {
    setSelectedCategories(prev => 
      prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]
    );
  };

  const toggleExamCategory = (cat: string) => {
    setSelectedExamCategories(prev => 
      prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]
    );
  };

  const getEventIcon = (type: string) => {
    switch (type) {
      case 'medication': return <Pill className="w-3.5 h-3.5" />;
      case 'examination': return <FileText className="w-3.5 h-3.5" />;
      case 'allergy': return <ShieldAlert className="w-3.5 h-3.5" />;
      case 'clinical-event': return <Activity className="w-3.5 h-3.5" />;
      default: return <CalendarIcon className="w-3.5 h-3.5" />;
    }
  };

  const getEventColor = (type: string) => {
    switch (type) {
      case 'medication': return 'text-blue-600 bg-blue-50 dark:bg-blue-900/20 border-blue-100 dark:border-blue-800/30';
      case 'examination': return 'text-indigo-600 bg-indigo-50 dark:bg-indigo-900/20 border-indigo-100 dark:border-indigo-800/30';
      case 'allergy': return 'text-red-600 bg-red-50 dark:bg-red-900/20 border-red-100 dark:border-red-800/30';
      case 'clinical-event': return 'text-amber-600 bg-amber-50 dark:bg-amber-900/20 border-amber-100 dark:border-amber-800/30';
      default: return 'text-gray-600 bg-gray-50 dark:bg-dark-bg border-gray-100 dark:border-dark-border';
    }
  };

  const handleEventClick = (event: CalendarEvent) => {
    if (onEventClick) {
        onEventClick(event);
        return;
    }
    setSelectedEvent(event);
  };

  const navigateToDetail = (event: CalendarEvent) => {
    if (event.type === 'medication' && (event.originalData?.code?.catalog_id || event.originalData?.id)) {
        const id = event.originalData.code?.catalog_id || event.originalData.id;
        navigate(`/medications/details/${id}`);
    } else if (event.type === 'examination') {
        navigate(`/examinations/${event.id}`);
    } else if (event.type === 'clinical-event') {
        navigate(`/events/${event.originalData.id || event.id.split('-')[0]}`);
    }
    setSelectedEvent(null);
  };

  const renderDayDetails = () => {
    const selectedEvents = displayEvents.filter(e => isSameDay(e.date, selectedDate));
    
    return (
        <div className="p-4 bg-blue-50/10 dark:bg-blue-900/5 border-t border-blue-500/10 h-full flex flex-col min-h-0">
            <div className="flex items-center justify-between mb-3 shrink-0">
                <h3 className="text-[10px] font-black text-blue-600/70 dark:text-blue-400/70 uppercase tracking-[0.2em] flex items-center">
                    <Clock className="w-3.5 h-3.5 mr-2" />
                    {t('medications.calendar.schedule_for')} {selectedDate.toLocaleDateString(i18n.language, { month: 'short', day: 'numeric', year: 'numeric' })}
                </h3>
                <div className="px-2 py-0.5 bg-blue-100/50 dark:bg-blue-900/30 rounded-lg text-[9px] font-black text-blue-600 dark:text-blue-300 uppercase tracking-widest">
                    {selectedEvents.length} {t('common.events')}
                </div>
            </div>
            <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar min-h-0">
                {selectedEvents.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-6 opacity-40">
                        <Check className="w-8 h-8 mb-2 text-gray-300" />
                        <p className="text-xs text-gray-400 italic font-medium text-center">{t('medications.calendar.no_doses')}</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                        {selectedEvents.map((event, idx) => (
                            <div 
                              key={idx} 
                              onClick={() => handleEventClick(event)}
                              className="flex items-center space-x-3 p-3 bg-white dark:bg-dark-surface rounded-2xl border border-gray-100 dark:border-dark-border shadow-sm hover:border-blue-300 dark:hover:border-blue-900 transition-all cursor-pointer group active:scale-[0.98]"
                            >
                                <div className={`p-2 rounded-xl border ${getEventColor(event.type)} shrink-0`}>
                                    {getEventIcon(event.type)}
                                </div>
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center space-x-2 mb-0.5">
                                      <p className="text-xs font-black text-gray-900 dark:text-dark-text truncate group-hover:text-blue-600 transition-colors">{event.title}</p>
                                    </div>
                                    <p className="text-[10px] text-gray-400 font-bold truncate">{event.subtitle || t('medications.calendar.standard_dose')}</p>
                                </div>
                                <div className="text-right flex flex-col items-end shrink-0">
                                    {event.time && event.time !== 'Unspecified' && (
                                        <div className="px-2 py-0.5 bg-blue-50 dark:bg-blue-900/20 rounded-md border border-blue-100 dark:border-blue-800/30 mb-1">
                                            <p className="text-[10px] font-black text-blue-600 dark:text-blue-400">{event.time}</p>
                                        </div>
                                    )}
                                    <ExternalLink className="w-3 h-3 text-gray-300 group-hover:text-blue-500 transition-all" />
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
  };

  const renderClassic = () => {
    const days = eachDayOfInterval({ start: startDate, end: endDate });
    const dateNames = [
        t('medications.calendar.days.sun'),
        t('medications.calendar.days.mon'),
        t('medications.calendar.days.tue'),
        t('medications.calendar.days.wed'),
        t('medications.calendar.days.thu'),
        t('medications.calendar.days.fri'),
        t('medications.calendar.days.sat')
    ];

    return (
      <div className="flex flex-col h-full min-h-0 animate-in fade-in duration-500 overflow-hidden">
        <div className="grid grid-cols-7 border-b border-gray-50 dark:border-dark-border bg-gray-50/30 dark:bg-dark-bg/10 shrink-0">
          {dateNames.map((d, i) => (
            <div key={i} className="py-2 text-[10px] font-black text-gray-400 uppercase tracking-widest text-center">
              {d}
            </div>
          ))}
        </div>
        <div className={`grid grid-cols-7 content-start shrink-0 border-b border-gray-50 dark:border-dark-border ${
          fitToContainer ? "flex-1 min-h-0" : "overflow-y-auto custom-scrollbar max-h-[70%]"
        }`}>
          {days.map(day => {
            const isCurrentMonth = isSameMonth(day, monthStart);
            const dayEvents = displayEvents.filter(e => isSameDay(e.date, day));
            const isToday = isSameDay(day, new Date());
            const isSelected = isSameDay(day, selectedDate);

            return (
              <div
                key={day.toISOString()}
                className={`${
                    fitToContainer ? "flex-1 h-full" : "min-h-[140px] max-h-[180px]"
                } overflow-hidden p-2 border-r border-b border-gray-50 dark:border-dark-border transition-all cursor-pointer hover:bg-blue-50/30 dark:hover:bg-blue-900/5 ${
                  !isCurrentMonth ? "bg-gray-50/10 dark:bg-dark-bg/5 opacity-30" : ""
                } ${isSelected ? "ring-2 ring-inset ring-blue-500/20 bg-blue-50/10" : ""}`}
                onClick={() => setSelectedDate(day)}
              >
                <div className="flex justify-between items-start">
                    <span className={`text-xs font-black ${
                        isToday 
                        ? "bg-blue-600 text-white w-6 h-6 rounded-lg flex items-center justify-center -mt-1 -ml-1 shadow-md" 
                        : "text-gray-500"
                    }`}>
                        {format(day, "d")}
                    </span>
                    {dayEvents.length > 0 && (
                        <span className="text-[10px] font-black text-blue-500 bg-blue-50 dark:bg-blue-900/20 px-1.5 py-0.5 rounded-lg border border-blue-100 dark:border-blue-800/30">
                            {dayEvents.length}
                        </span>
                    )}
                </div>
                <div className="mt-2 space-y-1">
                  {dayEvents.slice(0, 3).map((event, idx) => (
                    <div 
                      key={idx} 
                      onClick={(e) => { e.stopPropagation(); handleEventClick(event); }}
                      className={`text-[9px] px-1.5 py-1 rounded-lg border truncate flex items-center font-bold hover:scale-[1.02] transition-transform ${getEventColor(event.type)}`}
                    >
                      <span className="truncate">
                        {event.time && event.time !== 'Unspecified' && <span className="mr-1">{event.time}</span>}
                        {event.title}
                      </span>
                    </div>
                  ))}
                  {dayEvents.length > 3 && (
                    <div className="text-[8px] font-black text-gray-400 text-center uppercase tracking-tighter">
                      + {dayEvents.length - 3} {t('medications.calendar.more')}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        <div className={`${fitToContainer ? "h-36 sm:h-44" : "shrink-0 flex-1"} min-h-0 overflow-y-auto`}>
            {renderDayDetails()}
        </div>
      </div>
    );
  };

  const renderTimeline = () => {
    const days = eachDayOfInterval({ start: startDate, end: endDate });

    return (
      <div className="flex flex-col h-full min-h-0 animate-in slide-in-from-right-4 duration-500 overflow-hidden">
        <div 
          ref={timelineRef}
          onMouseDown={handleTimelineMouseDown}
          onMouseMove={handleTimelineMouseMove}
          onMouseUp={handleTimelineMouseUp}
          onMouseLeave={handleTimelineMouseUp}
          className={`flex-1 overflow-x-auto pb-6 custom-scrollbar flex space-x-6 px-4 py-6 min-h-0 nodrag ${isDraggingTimeline ? 'cursor-grabbing select-none' : 'cursor-grab'}`}
        >
          {days.map(day => {
            const dayEvents = displayEvents.filter(e => isSameDay(e.date, day));
            const isToday = isSameDay(day, new Date());
            return (
              <div key={day.toISOString()} className="w-56 sm:w-64 shrink-0 flex flex-col h-full min-h-0">
                <div className={`p-4 rounded-2xl mb-4 transition-all shrink-0 ${isToday ? 'bg-blue-600 text-white shadow-xl shadow-blue-100' : 'bg-gray-50 dark:bg-dark-bg text-gray-400 dark:text-dark-muted border border-gray-100 dark:border-dark-border'}`}>
                  <p className="text-[10px] font-black uppercase tracking-widest opacity-70">{format(day, 'EEEE')}</p>
                  <p className="text-xl font-black">{format(day, 'MMM d')}</p>
                </div>
                
                <div className="space-y-3 overflow-y-auto flex-1 pr-1 custom-scrollbar min-h-0">
                  {dayEvents.length > 0 ? dayEvents.map(event => (
                    <button 
                      key={event.id}
                      onClick={() => {
                        if (dragInfo.current.hasMoved) return;
                        handleEventClick(event);
                      }}
                      className={`w-full text-left p-4 rounded-2xl border bg-white dark:bg-dark-surface hover:shadow-lg transition-all group/event active:scale-[0.98] ${
                        event.type === 'medication' ? 'border-blue-100 hover:border-blue-400' : 
                        event.type === 'allergy' ? 'border-red-100 hover:border-red-400' : 
                        'border-indigo-100 hover:border-indigo-400'
                      }`}
                    >
                      <div className="flex justify-between items-start mb-2">
                        <div className={`p-2 rounded-xl ${
                          event.type === 'medication' ? 'bg-blue-50 text-blue-500' : 
                          event.type === 'allergy' ? 'bg-red-50 text-red-500' : 
                          'bg-indigo-50 text-indigo-500'
                        }`}>
                          {getEventIcon(event.type)}
                        </div>
                        <div className="flex items-center space-x-1 font-bold text-gray-400 text-[10px]">
                          <Clock className="w-3 h-3" />
                          <span>{(event.time && event.time !== 'Unspecified') ? event.time : 'All day'}</span>
                        </div>
                      </div>
                      <p className="font-black text-gray-900 dark:text-dark-text group-hover/event:text-blue-600 transition-colors truncate text-sm mb-1">{event.title}</p>
                      {event.subtitle && <p className="text-[11px] text-gray-500 dark:text-dark-muted leading-relaxed line-clamp-2">{event.subtitle}</p>}
                    </button>
                  )) : (
                    <div className="flex flex-col items-center justify-center bg-gray-50/50 dark:bg-dark-bg/30 rounded-3xl border border-dashed border-gray-200 dark:border-dark-border py-12">
                      <Check className="w-8 h-8 text-gray-200 dark:text-dark-muted mb-2" />
                      <p className="text-[8px] font-black text-gray-400 dark:text-dark-muted uppercase tracking-tighter">No events</p>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  const renderList = () => {
    const sortedEvents = [...displayEvents].sort((a, b) => a.date.getTime() - b.date.getTime());
    const upcoming = sortedEvents.filter(e => e.date >= startOfDay(new Date()));

    return (
      <div className="flex flex-col h-full p-6 animate-in fade-in duration-500">
        <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar space-y-4">
          {upcoming.length > 0 ? upcoming.map((event, idx) => {
            const isNewDay = idx === 0 || !isSameDay(event.date, upcoming[idx-1].date);
            return (
              <div key={event.id} className="space-y-2">
                {isNewDay && (
                  <div className="sticky top-0 z-10 py-2 bg-white/80 dark:bg-dark-surface/80 backdrop-blur-sm">
                    <span className="text-[10px] font-black text-blue-600 uppercase tracking-widest">{format(event.date, 'EEEE, MMMM d, yyyy')}</span>
                  </div>
                )}
                <button 
                  onClick={() => handleEventClick(event)}
                  className="w-full flex items-center p-4 bg-gray-50/50 dark:bg-dark-bg/50 hover:bg-white dark:hover:bg-dark-surface border border-transparent hover:border-blue-100 dark:hover:border-blue-900 rounded-2xl transition-all group/item"
                >
                  <div className={`p-3 rounded-xl mr-4 ${getEventColor(event.type)}`}>
                    {getEventIcon(event.type)}
                  </div>
                  <div className="flex-1 text-left min-w-0">
                    <h4 className="font-black text-gray-900 dark:text-dark-text group-hover/item:text-blue-600 transition-colors truncate">{event.title}</h4>
                    <p className="text-xs text-gray-400 font-bold uppercase tracking-tight">{(event.time && event.time !== 'Unspecified') ? event.time : 'All day'} • {event.type}</p>
                  </div>
                  <ChevronRight className="w-5 h-5 text-gray-300 group-hover/item:text-blue-500 transition-all group-hover/item:translate-x-1" />
                </button>
              </div>
            );
          }) : (
            <div className="flex flex-col items-center justify-center py-20 opacity-30">
              <CalendarIcon className="w-16 h-16 mb-4" />
              <p className="text-lg font-black uppercase tracking-widest">No upcoming events</p>
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderHistory = () => {
    const sortedEvents = [...displayEvents].sort((a, b) => b.date.getTime() - a.date.getTime());
    const past = sortedEvents.filter(e => e.date < startOfDay(new Date()));

    return (
      <div className="flex flex-col h-full relative p-8 animate-in fade-in duration-500">
        <div className="absolute left-[39px] top-0 bottom-0 w-px bg-gray-100 dark:bg-dark-border" />
        <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar space-y-8 pb-10">
          {past.length > 0 ? past.map((event) => (
            <div key={event.id} className="relative pl-14 group/history">
              <div className={`absolute left-0 top-1 w-[56px] h-[56px] rounded-2xl flex items-center justify-center z-10 border-4 border-white dark:border-dark-surface shadow-lg transition-all group-hover/history:scale-110 ${
                event.type === 'medication' ? 'bg-blue-500 text-white' : 
                event.type === 'allergy' ? 'bg-red-500 text-white' : 
                'bg-indigo-500 text-white'
              }`}>
                {getEventIcon(event.type)}
              </div>
              <div className="bg-white dark:bg-dark-surface border border-gray-100 dark:border-dark-border rounded-[2rem] p-6 hover:shadow-2xl hover:border-blue-200 transition-all cursor-pointer" onClick={() => handleEventClick(event)}>
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <span className="text-[10px] font-black text-blue-600 uppercase tracking-widest block mb-1">{format(event.date, 'MMM d, yyyy')} @ {(event.time && event.time !== 'Unspecified') ? event.time : 'All day'}</span>
                    <h4 className="text-xl font-black text-gray-900 dark:text-dark-text">{event.title}</h4>
                  </div>
                  <span className={`px-3 py-1 rounded-full text-[9px] font-black uppercase tracking-wider ${getEventColor(event.type)}`}>{event.type}</span>
                </div>
                {event.subtitle && <p className="text-sm text-gray-500 dark:text-dark-muted leading-relaxed line-clamp-2 italic">"{event.subtitle}"</p>}
                <div className="mt-4 pt-4 border-t border-gray-50 dark:border-dark-border flex items-center text-[10px] font-black text-blue-600 uppercase tracking-widest hover:text-black transition-colors">
                  <span>{t('common.details')}</span>
                  <ChevronRight className="w-3 h-3 ml-1" />
                </div>
              </div>
            </div>
          )) : (
            <div className="flex flex-col items-center justify-center py-20 opacity-30">
              <TrendingUp className="w-16 h-16 mb-4" />
              <p className="text-lg font-black uppercase tracking-widest">No past events</p>
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className={`flex flex-col w-full h-full ${transparent ? 'min-h-0' : `bg-white dark:bg-dark-surface rounded-[2.5rem] shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden min-h-0 ${compact ? 'max-w-2xl' : 'w-full'}`}`}>
      {!hideHeader ? (
        <div className={`px-6 py-4 border-b border-gray-50 dark:border-dark-border flex flex-col lg:flex-row lg:items-center justify-between gap-4 ${transparent ? 'bg-transparent' : ''}`}>
          <div className={`flex items-center space-x-4 ${titleTo ? 'group/title relative cursor-pointer hover:opacity-80 transition-opacity' : ''}`}
               onClick={titleTo ? (e) => { e.stopPropagation(); navigate(titleTo); } : undefined}
               title={titleTo ? title : undefined}>
            <div className="p-3 bg-blue-50 dark:bg-blue-900/30 rounded-2xl">
              <CalendarIcon className="w-6 h-6 text-blue-600" />
            </div>
            <div>
              <h2 className="text-xl font-black text-brand-navy dark:text-dark-text tracking-tight uppercase leading-none flex items-center space-x-1.5">
                <span>{title || (viewType === 'classic' ? format(currentDate, 'MMMM yyyy') : t('common.calendar'))}</span>
                {titleTo && (
                  <ExternalLink
                    onClick={(e) => { e.stopPropagation(); e.preventDefault(); window.open(titleTo, '_blank', 'noopener,noreferrer'); }}
                    className="w-4 h-4 text-gray-400 dark:text-dark-muted opacity-0 group-hover/title:opacity-100 hover:!text-blue-500 transition-opacity shrink-0"
                  />
                )}
              </h2>
              {subtitle ? (
                <p className="text-[10px] text-gray-400 font-bold uppercase tracking-widest mt-1">{subtitle}</p>
              ) : (
                <div className="flex items-center space-x-2 mt-1">
                    <span className="text-xs font-bold text-gray-400 uppercase tracking-widest">
                        {displayEvents.length} {t('common.events')}
                    </span>
                    {loading && <div className="w-2.5 h-2.5 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin"></div>}
                </div>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {viewType === 'classic' && (
              <div className="flex items-center bg-gray-50 dark:bg-dark-bg p-1 rounded-xl border border-gray-100 dark:border-dark-border">
                  <button onClick={() => setCurrentDate(subMonths(currentDate, 1))} className="p-1.5 hover:bg-white dark:hover:bg-dark-surface rounded-lg transition-all shadow-sm">
                      <ChevronLeft className="w-3.5 h-3.5 text-gray-600" />
                  </button>
                  <button onClick={() => setCurrentDate(new Date())} className="px-3 text-[10px] font-black text-gray-700 dark:text-dark-text uppercase hover:text-blue-600 transition-colors">
                      {t('common.today')}
                  </button>
                  <button onClick={() => setCurrentDate(addMonths(currentDate, 1))} className="p-1.5 hover:bg-white dark:hover:bg-dark-surface rounded-lg transition-all shadow-sm">
                      <ChevronRight className="w-3.5 h-3.5 text-gray-600" />
                  </button>
              </div>
            )}

            <div className="relative group/search">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 group-focus-within/search:text-blue-500 transition-colors" />
              <input 
                type="text" 
                placeholder={t('common.search')}
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="pl-9 pr-3 py-2 bg-gray-50 dark:bg-dark-bg border border-gray-200 dark:border-dark-border focus:border-blue-500/30 focus:bg-white dark:focus:bg-dark-surface rounded-xl text-xs font-bold outline-none transition-all w-36 focus:w-48"
              />
            </div>

            <div className="relative" ref={filterDropdownRef}>
              <button 
                onClick={() => setIsFilterDropdownOpen(!isFilterDropdownOpen)}
                className={`flex items-center space-x-2 px-3 py-2 bg-gray-50 dark:bg-dark-bg text-gray-700 dark:text-dark-text rounded-xl text-[10px] font-black uppercase tracking-wider hover:bg-white dark:hover:bg-dark-surface transition-all border border-gray-200 dark:border-dark-border ${isFilterDropdownOpen ? 'bg-white dark:bg-dark-surface border-blue-500/30 shadow-lg' : ''}`}
              >
                <Filter className={`w-3.5 h-3.5 ${selectedCategories.length < 4 ? 'text-blue-600' : 'text-gray-400'}`} />
                <span>{t('common.filters')}</span>
                <ChevronDown className={`w-3 h-3 transition-transform duration-300 ${isFilterDropdownOpen ? 'rotate-180' : ''}`} />
              </button>
              
              {isFilterDropdownOpen && (
                <div className="absolute right-0 mt-2 w-56 bg-white dark:bg-dark-surface rounded-2xl shadow-2xl border border-gray-200 dark:border-dark-border z-[100] p-1.5 animate-in fade-in zoom-in-95 duration-200">
                  <div className="px-4 py-2 border-b border-gray-50 dark:border-dark-border mb-1">
                    <p className="text-[9px] font-black text-gray-400 uppercase tracking-[0.2em]">Include Categories</p>
                  </div>
                  {[
                    { id: 'medication' as CalendarEventType, icon: Pill, color: 'blue', label: t('common.medications') },
                    { id: 'allergy' as CalendarEventType, icon: ShieldAlert, color: 'red', label: 'Allergies' },
                    { id: 'examination' as CalendarEventType, icon: FileText, color: 'indigo', label: t('common.examinations') },
                    { id: 'clinical-event' as CalendarEventType, icon: Activity, color: 'amber', label: t('events.title') }
                  ].map(cat => (
                    <button
                      key={cat.id}
                      onClick={() => toggleCategory(cat.id as CalendarEventType)}
                      className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl text-xs font-bold transition-all ${
                        selectedCategories.includes(cat.id as CalendarEventType) 
                          ? `bg-blue-50 dark:bg-blue-900/20 text-blue-600` 
                          : 'text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5'
                      }`}
                    >
                      <div className="flex items-center space-x-3">
                        <cat.icon className="w-4 h-4" />
                        <span>{cat.label}</span>
                      </div>
                      {selectedCategories.includes(cat.id as CalendarEventType) && <Check className="w-3.5 h-3.5" />}
                    </button>
                  ))}

                  {selectedCategories.includes('examination') && examCategories.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-gray-100 dark:border-dark-border">
                      <p className="text-[9px] font-black text-gray-400 uppercase tracking-[0.2em] px-4 mb-1 flex justify-between items-center">
                        <span>Examination Types</span>
                        {selectedExamCategories.length > 0 && (
                          <button onClick={() => setSelectedExamCategories([])} className="text-blue-500 hover:text-blue-700 capitalize">Clear</button>
                        )}
                      </p>
                      <div className="max-h-48 overflow-y-auto custom-scrollbar px-1 space-y-0.5">
                          {examCategories.map(cat => (
                             <button
                               key={cat.id}
                               onClick={() => toggleExamCategory(cat.name)}
                               className={`w-full flex items-center justify-between px-3 py-1.5 rounded-lg text-[10px] font-bold transition-all ${
                                 selectedExamCategories.includes(cat.name) 
                                   ? `bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600` 
                                   : 'text-gray-500 dark:text-gray-400 hover:bg-gray-50'
                               }`}
                             >
                               <span className="truncate">{cat.name}</span>
                               {selectedExamCategories.includes(cat.name) && <Check className="w-3 h-3" />}
                             </button>
                          ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="relative" ref={viewDropdownRef}>
              <button 
                onClick={() => setIsViewDropdownOpen(!isViewDropdownOpen)}
                className="flex items-center space-x-2 px-3 py-2 bg-brand-navy text-white rounded-xl text-[10px] font-black uppercase tracking-wider hover:bg-black transition-all shadow-lg active:scale-95"
              >
                {viewType === 'timeline' && <Layout className="w-3.5 h-3.5" />}
                {viewType === 'classic' && <Grid className="w-3.5 h-3.5" />}
                {viewType === 'list' && <List className="w-3.5 h-3.5" />}
                {viewType === 'history' && <TrendingUp className="w-3.5 h-3.5" />}
                <span>{viewType} View</span>
                <ChevronDown className={`w-2.5 h-2.5 transition-transform duration-200 ${isViewDropdownOpen ? 'rotate-180' : ''}`} />
              </button>
              
              {isViewDropdownOpen && (
                <div className="absolute right-0 mt-2 w-48 bg-white dark:bg-dark-surface rounded-2xl shadow-2xl border border-gray-200 dark:border-dark-border z-[100] p-1.5 animate-in fade-in zoom-in-95 duration-200">
                   {[
                     { id: 'timeline', icon: Layout, label: 'Timeline' },
                     { id: 'classic', icon: Grid, label: 'Calendar' },
                     { id: 'list', icon: List, label: 'Schedule' },
                     { id: 'history', icon: TrendingUp, label: 'History' }
                   ].map(mode => (
                     <button 
                       key={mode.id}
                       onClick={() => {
                         setViewType(mode.id as ViewType);
                         setIsViewDropdownOpen(false);
                       }}
                       className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-xl text-xs transition-all ${viewType === mode.id ? 'bg-blue-600 text-white shadow-lg' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5'}`}
                     >
                       <mode.icon className="w-3.5 h-3.5" />
                       <span className="font-bold">{mode.label}</span>
                     </button>
                   ))}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}

      <div className={`flex-1 min-h-0 flex flex-col overflow-hidden ${transparent ? 'bg-transparent' : 'bg-white dark:bg-dark-surface'}`}>
        {viewType === 'classic' && renderClassic()}
        {viewType === 'timeline' && renderTimeline()}
        {viewType === 'list' && renderList()}
        {viewType === 'history' && renderHistory()}
      </div>

      {selectedEvent && (
        renderModal ? (
          renderModal(selectedEvent, () => setSelectedEvent(null))
        ) : (
          <Portal>
            <div className="fixed inset-0 bg-black/60 backdrop-blur-md z-[150] flex items-center justify-center p-4 animate-in fade-in duration-300">
              <div className="bg-white dark:bg-dark-surface rounded-[2.5rem] w-full max-w-lg shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300 border border-white/20">
                <div className={`p-8 text-white relative ${
                  selectedEvent.type === 'medication' ? 'bg-blue-600' : 
                  selectedEvent.type === 'allergy' ? 'bg-red-600' : 'bg-indigo-600'
                }`}>
                  <button onClick={() => setSelectedEvent(null)} className="absolute top-6 right-6 p-2 hover:bg-white/20 rounded-full transition-colors">
                    <X className="w-5 h-5" />
                  </button>
                  <div className="flex items-center space-x-4 mb-2">
                    <div className="p-4 bg-white/20 rounded-2xl backdrop-blur-md border border-white/20">
                      {getEventIcon(selectedEvent.type)}
                    </div>
                    <div>
                      <h2 className="text-3xl font-black tracking-tight">{selectedEvent.title}</h2>
                      <p className="text-white/70 text-sm font-bold uppercase tracking-widest">{selectedEvent.type} {t('common.details')}</p>
                    </div>
                  </div>
                </div>
                
                <div className="p-10 space-y-8 max-h-[60vh] overflow-y-auto no-scrollbar text-gray-900 dark:text-dark-text">
                  <div className="flex items-center space-x-6">
                    <div className="flex flex-col">
                      <span className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">Date</span>
                      <span className="text-lg font-black">{format(selectedEvent.date, 'MMMM d, yyyy')}</span>
                    </div>
                    <div className="w-px h-10 bg-gray-100 dark:bg-dark-border" />
                    <div className="flex flex-col">
                      <span className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">Time</span>
                      <span className="text-lg font-black">{selectedEvent.time && selectedEvent.time !== 'Unspecified' ? selectedEvent.time : 'All Day'}</span>
                    </div>
                  </div>
                  <div>
                    <h4 className="flex items-center space-x-2 text-[10px] font-black text-gray-400 uppercase tracking-widest mb-3">
                      <Info className="w-4 h-4" />
                      <span>Clinical Details</span>
                    </h4>
                    <div className="p-6 bg-gray-50 dark:bg-dark-bg rounded-[2rem] border border-gray-100 dark:border-dark-border">
                      <p className="leading-relaxed font-medium">
                        {selectedEvent.subtitle || 'No additional clinical details available for this record.'}
                      </p>
                    </div>
                  </div>
                  {selectedEvent.type === 'medication' && selectedEvent.originalData?.reason && (
                    <div>
                      <h4 className="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-3">Indication / Reason</h4>
                      <p className="text-gray-600 dark:text-dark-muted italic leading-relaxed">"{selectedEvent.originalData.reason}"</p>
                    </div>
                  )}
                </div>
                <div className="p-8 bg-gray-50 dark:bg-dark-bg border-t border-gray-100 dark:border-dark-border flex justify-end space-x-4">
                  <button onClick={() => setSelectedEvent(null)} className="px-10 py-4 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border text-gray-900 dark:text-dark-text font-black text-xs uppercase tracking-widest rounded-2xl hover:bg-gray-100 dark:hover:bg-dark-border transition-all shadow-sm active:scale-95">
                    {t('common.dismiss')}
                  </button>
                  <button onClick={() => navigateToDetail(selectedEvent)} className="px-10 py-4 bg-brand-navy text-white font-black text-xs uppercase tracking-widest rounded-2xl hover:bg-black transition-all shadow-xl active:scale-95 flex items-center">
                    Go to Record
                    <ChevronRight className="w-4 h-4 ml-2" />
                  </button>
                </div>
              </div>
            </div>
          </Portal>
        )
      )}
    </div>
  );
};
