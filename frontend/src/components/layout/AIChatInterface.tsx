import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, MessageSquare, Sparkles, BarChart2, Send, Loader2, Bot, User, Database, ChevronRight, History, Plus, Maximize2, Minimize2, Wrench } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { streamAIAssistance, resumeHitlSession, AIStreamMessage, listChatSessions, getChatSessionMessages, deleteChatSession, ChatSession } from '../../services/aiAssistanceService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { format } from 'date-fns';
import { useNavigate } from 'react-router-dom';
import { CLINICAL_WORKFLOWS, ClinicalAction } from '../../config/clinicalWorkflows';
import { useTranslation } from 'react-i18next';
import { useIsMobile } from '../../hooks/useMediaQuery';
import { useChatAttachments } from '../../hooks/useChatAttachments';
import { toast } from 'react-toastify';

import { ToolCallInfo, Message, TaskInfo, PendingChatAttachment } from '../../types/ai';
import { CitationButton } from '../ai/CitationButton';
import { ChatInspector } from '../ai/ChatInspector';
import { ChatHistoryOverlay } from '../ai/ChatHistoryOverlay';
import { ChatLedgerOverlay } from '../ai/ChatLedgerOverlay';
import { AIToolsModal } from '../ai/AIToolsModal';
import { HitlTaskCard } from '../ai/hitl/HitlTaskCard';
import { TERMINAL_HITL_STATUSES } from '../ai/hitl/registry';
import { AIBadge } from '../ui/AIBadge';
import { ChatAttachmentPicker, ChatAttachmentPreviewRail } from '../ai/ChatAttachmentPicker';
import { ChatMessageImages } from '../ai/ChatMessageImages';

interface Props {
  isFullScreen?: boolean;
  onClose?: () => void;
  initialSessionId?: string;
  hideHeader?: boolean;
  activeTab?: 'chat' | 'insights' | 'actions';
  onTabChange?: (tab: 'chat' | 'insights' | 'actions') => void;
  interfaceRef?: React.RefObject<AIChatHandlers>;
}

export interface AIChatHandlers {
  startNewChat: () => void;
  toggleHistory: () => void;
  toggleLedger: () => void;
  toggleToolsModal: () => void;
}

/**
 * Map a streamed AI error to a localized, user-facing message.
 *
 * The backend classifies LLM/provider errors into a stable ``errorType`` code
 * (connection / timeout / auth / rate_limit / generic) and intentionally
 * leaves ``error`` EMPTY so raw SDK text (e.g. OpenAI's "Connection error.")
 * never reaches the user. Soft guard violations use ``errorType === 'guard'``
 * with the user-facing message in ``error``.
 *
 * Falls back to the raw ``error`` text (legacy/unguarded) or a generic
 * localized message so we never render an empty bubble.
 */
function resolveStreamErrorMessage(
  t: (key: string, options?: Record<string, unknown>) => string,
  msg: AIStreamMessage,
): string {
  if (msg.errorType === 'guard' && msg.error) {
    return msg.error;
  }
  switch (msg.errorType) {
    case 'connection':
    case 'timeout':
      return t('ai_chat.errors.connection');
    case 'auth':
      return t('ai_chat.errors.auth');
    case 'rate_limit':
      return t('ai_chat.errors.rate_limit');
    default:
      return msg.error || t('ai_chat.errors.generic');
  }
}

export const AIChatInterface: React.FC<Props> = ({ 
  isFullScreen = false, 
  onClose, 
  initialSessionId, 
  hideHeader = false,
  activeTab: externalTab,
  onTabChange,
  interfaceRef
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [internalTab, setInternalTab] = useState<'chat' | 'insights' | 'actions'>('chat');
  
  const activeTab = externalTab || internalTab;
  const setActiveTab = (tab: 'chat' | 'insights' | 'actions') => {
    if (onTabChange) onTabChange(tab);
    else setInternalTab(tab);
  };

  const [messages, setMessages] = useState<Message[]>([
    { role: 'assistant', content: t('ai_chat.welcome_message') }
  ]);
  const [userInput, setUserInput] = useState('');
  /** Pending image attachments staged in the composer (not yet sent). */
  const [pendingAttachments, setPendingAttachments] = useState<PendingChatAttachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { addFiles } = useChatAttachments((m: string) => toast.warning(m));

  /** Add picked/dropped/pasted files to the composer (validated + encoded). */
  const handleFilesAdded = useCallback(
    async (files: FileList | File[]) => {
      const next = await addFiles(files, pendingAttachments);
      setPendingAttachments(next);
    },
    [addFiles, pendingAttachments],
  );
  const [loading, setLoading] = useState(false);
  const loadingRef = useRef(false);
  // Tracks assistant message indices we've already auto-resumed, so the
  // continuation fires at most once per resolved task cluster. Storing ids
  // rather than a flag on the Message prevents double-fire on re-renders.
  const resumedMessageIds = useRef<Set<string>>(new Set());

  // Auto-resize textarea logic
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = '0px';
      const scrollHeight = textareaRef.current.scrollHeight;
      // Constrain height between 72px and 200px
      const newHeight = Math.min(Math.max(scrollHeight, 72), 200);
      textareaRef.current.style.height = `${newHeight}px`;
    }
  }, [userInput]);
  
  const [inspectingTool, setInspectingTool] = useState<ToolCallInfo | null>(null);
  const [inspectorViewMode, setInspectorViewMode] = useState<'raw' | 'table'>('table');
  const [isLedgerOpen, setIsLedgerOpen] = useState(false);
  const [isToolsModalOpen, setIsToolsModalOpen] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const currentSessionIdRef = useRef<string | null>(null);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);

  // Expose handlers via ref
  React.useImperativeHandle(interfaceRef, () => ({
    startNewChat,
    toggleHistory: () => setIsHistoryOpen(prev => !prev),
    toggleLedger: () => setIsLedgerOpen(prev => !prev),
    toggleToolsModal: () => setIsToolsModalOpen(prev => !prev)
  }));
  
  const currentPatient = usePatientStore(state => state.currentPatient);
  const currentExaminationId = useUIStore(state => state.currentExaminationId);
  const currentBiomarkerId = useUIStore(state => state.currentBiomarkerId);
  const currentMedicationId = useUIStore(state => state.currentMedicationId);
  const pendingAIMessage = useUIStore(state => state.pendingAIMessage);
  const setPendingAIMessage = useUIStore(state => state.setPendingAIMessage);
  const lastNonAiPath = useUIStore(state => state.lastNonAiPath);
  const setAIDrawerOpen = useUIStore(state => state.setAIDrawerOpen);
  const currentAiSessionId = useUIStore(state => state.currentAiSessionId);
  const setCurrentAiSessionId = useUIStore(state => state.setCurrentAiSessionId);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const uniqueSourcesCount = Array.from(new Set(messages.flatMap(m => m.toolCalls || []).filter(tc => tc.status === 'finished').map(tc => tc.name))).length;

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    fetchSessions();
  }, [currentPatient]);

  useEffect(() => {
    if (pendingAIMessage) {
      handleSendMessage(undefined, pendingAIMessage);
      setPendingAIMessage(null);
    }
  }, [pendingAIMessage]);

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
    setCurrentAiSessionId(currentSessionId);
  }, [currentSessionId, setCurrentAiSessionId]);

  useEffect(() => {
    // Sync local currentSessionId with store if it changes elsewhere
    if (!loadingRef.current && currentAiSessionId !== currentSessionId) {
      if (currentAiSessionId) {
         loadSession(currentAiSessionId);
      } else if (currentSessionId) {
         // Clear local messages if global session is nullified
         setMessages([{ role: 'assistant', content: t('ai_chat.welcome_message') }]);
         setCurrentSessionId(null);
      }
    }
  }, [currentAiSessionId]);

  useEffect(() => {
    // Priority 1: Initial Session ID from Props (URL params)
    if (initialSessionId) {
      if (initialSessionId !== currentSessionIdRef.current) {
        loadSession(initialSessionId);
      }
    } 
    // Priority 2: Persistent Session ID from UI Store (Shared between Drawer and FullScreen)
    else if (!isFullScreen && currentAiSessionId && currentAiSessionId !== currentSessionIdRef.current) {
      loadSession(currentAiSessionId);
    }
    // New chat / Home navigation
    else if (currentSessionIdRef.current && isFullScreen && !initialSessionId) {
      // User navigated back to base URL, so start new chat
      setMessages([{ role: 'assistant', content: t('ai_chat.welcome_message') }]);
      setCurrentSessionId(null);
      setIsHistoryOpen(false);
      setIsLedgerOpen(false);
    }
  }, [initialSessionId, isFullScreen]);


  // Keyboard listeners for closing overlays
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsHistoryOpen(false);
        setIsLedgerOpen(false);
        setInspectingTool(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const fetchSessions = async () => {
    try {
      const data = await listChatSessions(currentPatient?.id);
      setSessions(data);
    } catch (err) {
      console.error("Failed to fetch sessions", err);
    }
  };

  const startNewChat = () => {
    setMessages([{ role: 'assistant', content: t('ai_chat.welcome_message') }]);
    setCurrentSessionId(null);
    setIsHistoryOpen(false);
    setIsLedgerOpen(false);
    resumedMessageIds.current.clear();
    if (isFullScreen) {
      navigate('/ai-assistant');
    }
  };

  const loadSession = async (sessionId: string) => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const data = await getChatSessionMessages(sessionId);
      setMessages(data.map(m => ({
        role: m.role,
        content: m.content.text,
        images: m.content.images,
        toolCalls: m.tool_calls?.map(tc => ({
          name: tc.name,
          args: typeof tc.args === 'string' ? tc.args : JSON.stringify(tc.args),
          result: tc.result,
          status: 'finished'
        })),
        citations: m.citations,
        tasks: m.tasks,
        _loadedFromHistory: true
      })));
      setCurrentSessionId(sessionId);
      setIsHistoryOpen(false);
      setIsLedgerOpen(false);
      resumedMessageIds.current.clear();
      
      // Update URL if we are in full screen mode and it's not already in URL
      if (isFullScreen && initialSessionId !== sessionId) {
        navigate(`/ai-assistant/${sessionId}`);
      }
    } catch (err) {
      console.error("Failed to load session", err);
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteSession = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    try {
      await deleteChatSession(sessionId);
      setSessions(prev => prev.filter(s => s.id !== sessionId));
      if (currentSessionId === sessionId) {
        startNewChat();
      }
    } catch (err) {
      console.error("Failed to delete session", err);
    }
  };

  const handleSendMessage = async (e?: React.FormEvent, overrideContent?: string) => {
    if (e) e.preventDefault();
    // Allow sending with only images (no text) so a user can ask "what's this?".
    const hasImages = pendingAttachments.some(a => a.status === 'ready');
    if ((!userInput.trim() && !overrideContent && !hasImages) || loadingRef.current) return;

    const messageContent = overrideContent || userInput.trim();
    // Collect ready data URLs; anything still encoding is dropped (rare —
    // encoding is fast; the send button is disabled while encoding).
    const messageImages = pendingAttachments
      .filter(a => a.status === 'ready' && a.dataUrl)
      .map(a => a.dataUrl as string);
    if (!overrideContent) setUserInput('');
    setPendingAttachments([]);
    setLoading(true);
    loadingRef.current = true;

    const newUserMessage: Message = {
      role: 'user',
      content: messageContent,
      ...(messageImages.length ? { images: messageImages } : {}),
    };
    setMessages(prev => [...prev, newUserMessage]);

    let accumulatedContent = '';
    let accumulatedToolCalls: ToolCallInfo[] = [];
    let accumulatedCitations: string[] = [];
    let accumulatedTasks: TaskInfo[] = [];
    
    const placeholderMessage: Message = { role: 'assistant', content: '', toolCalls: [], citations: [], isExecuting: false };
    setMessages(prev => [...prev, placeholderMessage]);

    try {
      await streamAIAssistance(
        {
          task_type: 'chat',
          user_input: messageContent,
          images: messageImages.length ? messageImages : undefined,
          context: {
            patient_id: currentPatient?.id,
            examination_id: currentExaminationId,
            biomarker_id: currentBiomarkerId,
            medication_id: currentMedicationId,
            session_id: currentSessionId,
            current_tab: activeTab
          }
        },
        (msg: AIStreamMessage) => {
          if (msg.sessionId) {
            const isNewSession = msg.sessionId !== currentSessionIdRef.current;
            setCurrentSessionId(msg.sessionId);
            if (isNewSession) {
              fetchSessions();
              if (isFullScreen) {
                navigate(`/ai-assistant/${msg.sessionId}`);
              }
            }
          }
          
          if (msg.content) {
            accumulatedContent += msg.content;
          }

          if (msg.toolCall) {
            const { name, status, args, result } = msg.toolCall;
            if (name) {
              const existingIdx = accumulatedToolCalls.findIndex(tc => tc.name === name);
              if (existingIdx === -1) {
                accumulatedToolCalls.push({ name, status, args, result });
              } else {
                accumulatedToolCalls[existingIdx].status = status;
                if (args) accumulatedToolCalls[existingIdx].args = args;
                if (result) accumulatedToolCalls[existingIdx].result = result;
              }
            }
          }

          if (msg.citation) {
             if (!accumulatedCitations.includes(msg.citation)) {
               accumulatedCitations.push(msg.citation);
             }
          }

          if (msg.task) {
            // Replace any existing task with the same proposal_id, else append
            const idx = accumulatedTasks.findIndex(t => t.proposal_id === msg.task!.proposal_id);
            if (idx === -1) {
              accumulatedTasks.push(msg.task);
            } else {
              accumulatedTasks[idx] = msg.task;
            }
          }

          if (msg.error || msg.errorType) {
            // LLM/provider error (localized by code) or soft guard violation.
            accumulatedContent = resolveStreamErrorMessage(t, msg);
          }

          setMessages(prev => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            
            if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
              updated[lastIdx] = {
                ...updated[lastIdx],
                content: accumulatedContent,
                toolCalls: [...accumulatedToolCalls],
                citations: [...accumulatedCitations],
                tasks: accumulatedTasks.length ? [...accumulatedTasks] : updated[lastIdx].tasks,
                isExecuting: msg.toolCall ? msg.toolCall.status !== 'finished' : updated[lastIdx].isExecuting
              };
            }
            return updated;
          });
        },
        () => {
          setLoading(false);
          loadingRef.current = false;
        },
        (err) => {
          console.error("Streaming error:", err);
          setMessages(prev => {
            const updated = [...prev];
            updated[updated.length - 1] = { 
              ...updated[updated.length - 1],
              role: 'assistant', 
              content: t('ai_chat.status.error')
            };
            return updated;
          });
          setLoading(false);
          loadingRef.current = false;
        }
      );
    } catch (err) {
      setLoading(false);
      loadingRef.current = false;
    }
  };

  /**
   * Trigger a HITL continuation turn after the user has resolved all pending
   * task cards on an assistant message. Streams a new assistant message that
   * reacts to the outcomes (acknowledge, propose follow-ups, etc).
   *
   * Guardrails:
   *   - `loadingRef` gate prevents races with a concurrently-running user turn.
   *   - `resumedMessageIds` Set ensures we fire at most once per message,
   *     even if onResolved fires multiple times in the same tick.
   *   - We do NOT auto-resume across a session reload — only live resolutions
   *     (onResolved is only callable from interactive proposed cards; resolved
   *     cards collapse to a buttonless summary so this is naturally enforced).
   */
  const triggerResume = async (messageIndex: number, messageKey: string) => {
    const sessionId = currentSessionIdRef.current;
    if (!sessionId) return;
    // Race guard: don't fire a resume while another stream is running.
    if (loadingRef.current) {
      // Mark as resumed anyway so we don't retry when the in-flight stream ends.
      resumedMessageIds.current.add(messageKey);
      return;
    }
    resumedMessageIds.current.add(messageKey);

    setLoading(true);
    loadingRef.current = true;

    // Placeholder for the continuation assistant message.
    const placeholderMessage: Message = {
      role: 'assistant',
      content: '',
      toolCalls: [],
      citations: [],
      tasks: [],
      isExecuting: false,
    };
    setMessages(prev => [...prev, placeholderMessage]);

    let accumulatedContent = '';
    let accumulatedToolCalls: ToolCallInfo[] = [];
    let accumulatedCitations: string[] = [];
    let accumulatedTasks: TaskInfo[] = [];

    try {
      await resumeHitlSession(
        sessionId,
        {},
        (msg: AIStreamMessage) => {
          if (msg.content) {
            accumulatedContent += msg.content;
          }
          if (msg.toolCall) {
            const { name, status, args, result } = msg.toolCall;
            if (name) {
              const existingIdx = accumulatedToolCalls.findIndex(tc => tc.name === name);
              if (existingIdx === -1) {
                accumulatedToolCalls.push({ name, status, args, result });
              } else {
                accumulatedToolCalls[existingIdx].status = status;
                if (args) accumulatedToolCalls[existingIdx].args = args;
                if (result) accumulatedToolCalls[existingIdx].result = result;
              }
            }
          }
          if (msg.citation) {
            if (!accumulatedCitations.includes(msg.citation)) {
              accumulatedCitations.push(msg.citation);
            }
          }
          if (msg.task) {
            const idx = accumulatedTasks.findIndex(t => t.proposal_id === msg.task!.proposal_id);
            if (idx === -1) {
              accumulatedTasks.push(msg.task);
            } else {
              accumulatedTasks[idx] = msg.task;
            }
          }
          if (msg.error || msg.errorType) {
            // LLM/provider error (localized by code) or soft guard violation.
            // Previously this only forwarded raw ``msg.error`` text (which was
            // empty for provider errors) and never localized.
            accumulatedContent = resolveStreamErrorMessage(t, msg);
          }
          setMessages(prev => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
              updated[lastIdx] = {
                ...updated[lastIdx],
                content: accumulatedContent,
                toolCalls: [...accumulatedToolCalls],
                citations: [...accumulatedCitations],
                tasks: accumulatedTasks.length ? [...accumulatedTasks] : updated[lastIdx].tasks,
                isExecuting: msg.toolCall ? msg.toolCall.status !== 'finished' : updated[lastIdx].isExecuting
              };
            }
            return updated;
          });
        },
        () => {
          setLoading(false);
          loadingRef.current = false;
        },
        (err) => {
          console.error('HITL resume streaming error:', err);
          setMessages(prev => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            if (lastIdx >= 0) {
              updated[lastIdx] = {
                ...updated[lastIdx],
                role: 'assistant',
                content: t('ai_chat.hitl.resume_error', {
                  defaultValue: 'I could not continue after your review. Please try sending a message.'
                }),
              };
            }
            return updated;
          });
          setLoading(false);
          loadingRef.current = false;
        }
      );
    } catch (err) {
      setLoading(false);
      loadingRef.current = false;
    }
  };

  const handleExecuteAction = (action: ClinicalAction) => {
    setActiveTab('chat');
    const localizedPrompt = t(`ai_chat.actions.workflows.${action.id.replace(/-/g, '_')}.prompt`, { defaultValue: action.prompt });
    handleSendMessage(undefined, localizedPrompt);
  };

  const tabs = [
    { id: 'chat', icon: MessageSquare, label: t('ai_chat.tabs.chat') },
    { id: 'insights', icon: BarChart2, label: t('ai_chat.tabs.insights') },
    { id: 'actions', icon: Sparkles, label: t('ai_chat.tabs.actions') }
  ];

  return (
    <div className={`flex-1 flex flex-col bg-white dark:bg-dark-bg min-h-0 ${isFullScreen ? '' : 'rounded-none shadow-[-20px_0_50px_rgba(0,0,0,0.1)] border-l border-gray-100 dark:border-dark-border'}`}>
      
      {/* Integrated Menu Bar (Top, Non-scrollable) */}
      {!hideHeader && (
        <div className={`flex flex-col shrink-0 ${isFullScreen ? 'bg-white dark:bg-dark-surface text-gray-900 dark:text-white' : ''}`}>
          
          {/* Top Header Row */}
          <div className={`px-4 md:px-6 py-3 md:py-4 flex items-center justify-between ${isFullScreen ? 'border-b border-gray-100 dark:border-dark-border' : 'bg-indigo-600 text-white'}`}>
             <div className="flex items-center space-x-2 md:space-x-3">
                <div className={`p-1.5 md:p-2 rounded-lg md:rounded-xl ${isFullScreen ? 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400' : 'bg-white/20 text-white'}`}>
                   <Sparkles className="w-4 h-4 md:w-5 md:h-5" />
                </div>
                 <div className={isFullScreen ? 'hidden md:block' : ''}>
                    <h2 className="text-xs md:text-sm font-black uppercase tracking-[0.2em]">
                      {t('ai_chat.header.title')}
                    </h2>
                    {!isFullScreen && <p className="text-[8px] md:text-[10px] opacity-70 font-bold uppercase tracking-widest">{t('ai_chat.header.subtitle')}</p>}
                    {isFullScreen && <p className="text-[8px] md:text-[10px] text-gray-500 dark:text-dark-muted font-bold uppercase tracking-widest">{t('ai_chat.header.subtitle')}</p>}
                 </div>
              </div>

              {/* Tabs in Center for Full Screen */}
              {isFullScreen && (
                <div className="hidden sm:flex items-center bg-gray-100 dark:bg-dark-bg p-1 rounded-2xl border border-gray-200 dark:border-dark-border mx-2 md:mx-4">
                   {tabs.map(tab => (
                      <button
                       key={tab.id}
                       onClick={() => setActiveTab(tab.id as any)}
                       className={`flex items-center gap-1.5 md:gap-2 px-3 md:px-6 py-1.5 md:py-2 rounded-xl text-[9px] md:text-[10px] font-black uppercase tracking-widest transition-all ${
                         activeTab === tab.id 
                           ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-200 dark:shadow-none' 
                           : 'text-gray-500 dark:text-dark-muted hover:text-gray-700 dark:hover:text-dark-text hover:bg-white/50 dark:hover:bg-dark-surface'
                       }`}
                      >
                         <tab.icon className="w-3 h-3 md:w-3.5 md:h-3.5" />
                         <span className="hidden md:inline">{tab.label}</span>
                         <span className="md:hidden">{tab.label.substring(0, 3)}</span>
                      </button>
                   ))}
                </div>
              )}

              {/* Action Buttons & Badge */}
              <div className="flex items-center space-x-0.5 md:space-x-1">
                  <AIBadge taskType="chat" className="mr-2 md:mr-3" variant={isFullScreen ? 'default' : 'white'} />
                  
                  {isFullScreen && (
                   <button 
                     onClick={() => setIsHistoryOpen(!isHistoryOpen)}
                     className={`p-1.5 md:p-2 rounded-lg md:rounded-xl transition-all ${
                       isHistoryOpen 
                         ? 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400' 
                         : (isFullScreen 
                             ? 'text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-dark-muted dark:hover:text-dark-text dark:hover:bg-dark-bg' 
                             : 'text-white/70 hover:text-white hover:bg-white/10')
                     }`}
                     title={t('ai_chat.tooltips.history')}
                   >
                      <History className="w-4 h-4 md:w-5 md:h-5" />
                   </button>
                 )}
                 <button 
                   onClick={startNewChat}
                   className={`p-1.5 md:p-2 rounded-lg md:rounded-xl transition-all ${
                     isFullScreen 
                       ? 'text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-dark-muted dark:hover:text-dark-text dark:hover:bg-dark-bg' 
                       : 'text-white/70 hover:text-white hover:bg-white/10'
                   }`}
                   title={t('ai_chat.tooltips.new_chat')}
                 >
                    <Plus className="w-4 h-4 md:w-5 md:h-5" />
                 </button>
                 <button 
                   onClick={() => setIsToolsModalOpen(true)}
                   className={`p-1.5 md:p-2 rounded-lg md:rounded-xl transition-all ${
                     isFullScreen 
                       ? 'text-gray-500 hover:text-blue-600 hover:bg-blue-50 dark:text-dark-muted dark:hover:text-dark-text dark:hover:bg-dark-bg' 
                       : 'text-white/70 hover:text-white hover:bg-white/10'
                   }`}
                   title="View Agent Capabilities"
                 >
                    <Wrench className="w-4 h-4 md:w-5 md:h-5" />
                 </button>

                 <button 
                   onClick={() => setIsLedgerOpen(!isLedgerOpen)}
                   className={`relative p-1.5 md:p-2 rounded-lg md:rounded-xl transition-all ${
                     isLedgerOpen 
                       ? 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400' 
                       : (isFullScreen 
                           ? 'text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-dark-muted dark:hover:text-dark-text dark:hover:bg-dark-bg' 
                           : 'text-white/70 hover:text-white hover:bg-white/10')
                   }`}
                   title={t('ai_chat.tooltips.ledger')}
                 >
                    <Database className="w-4 h-4 md:w-5 md:h-5" />
                    {uniqueSourcesCount > 0 && (
                      <div className={`absolute top-0.5 md:top-1 right-0.5 md:right-1 w-3 h-3 md:w-4 md:h-4 text-[6px] md:text-[8px] font-black flex items-center justify-center rounded-full border-2 ${
                        isFullScreen 
                          ? 'bg-amber-500 text-white border-white dark:border-dark-surface' 
                          : 'bg-white text-indigo-600 border-indigo-600'
                      }`}>
                         {uniqueSourcesCount}
                      </div>
                    )}
                 </button>
                 
                 {!isFullScreen && (
                   <button 
                    onClick={() => {
                      onClose?.();
                      if (currentSessionId) {
                        navigate(`/ai-assistant/${currentSessionId}`);
                      } else {
                        navigate('/ai-assistant');
                      }
                    }}
                    className="p-1.5 md:p-2 text-white/70 hover:text-white hover:bg-white/10 rounded-lg md:rounded-xl transition-all"
                    title={t('ai_chat.tooltips.full_screen')}
                   >
                      <Maximize2 className="w-4 h-4 md:w-5 md:h-5" />
                   </button>
                 )}

                 {isFullScreen && (
                   <button 
                    onClick={() => {
                      // Force the drawer to open
                      setAIDrawerOpen(true);
                      
                      // Navigate back to the clinical context
                      // The AIDrawer component itself uses AIChatInterface, 
                      // and since currentSessionId is already set in the store or component state, 
                      // it will persist as long as the component doesn't unmount or is re-synced via initialSessionId.
                      navigate(lastNonAiPath || '/');
                    }}
                    className="p-1.5 md:p-2 text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-dark-muted dark:hover:text-dark-text dark:hover:bg-dark-bg rounded-lg md:rounded-xl transition-all"
                    title={t('ai_chat.tooltips.minimize')}
                   >
                      <Minimize2 className="w-4 h-4 md:w-5 md:h-5" />
                   </button>
                 )}

                 {onClose && (
                   <button 
                     onClick={onClose} 
                     className={`p-1.5 md:p-2 rounded-full transition-colors ${
                       isFullScreen 
                         ? 'text-gray-400 hover:text-gray-600 dark:text-dark-muted dark:hover:text-white hover:bg-gray-100 dark:hover:bg-dark-bg' 
                         : 'text-white/70 hover:text-white hover:bg-white/10'
                     }`}
                   >
                      <X className="w-4 h-4 md:w-5 md:h-5" />
                   </button>
                 )}
             </div>
          </div>

           {/* Tabs Row (Only for Drawer Mode or Mobile Full Screen) */}
           {isMobile && (
            <div className={`flex border-b border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/50 px-2 shrink-0 ${isFullScreen ? 'sm:hidden' : ''}`}>
              {tabs.map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as any)}
                  className={`flex-1 py-3 md:py-4 flex flex-col items-center justify-center space-y-1 relative transition-all ${
                    activeTab === tab.id 
                      ? 'text-indigo-600 dark:text-indigo-400' 
                      : 'text-gray-400 hover:text-gray-600 dark:text-dark-muted'
                  }`}
                >
                    <tab.icon className="w-3.5 h-3.5 md:w-4 md:h-4" />
                    <span className="text-[8px] md:text-[9px] font-black uppercase tracking-widest">{tab.label}</span>
                    {activeTab === tab.id && (
                      <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-indigo-600 rounded-full" />
                    )}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="flex-1 flex overflow-hidden relative min-h-0">
        {/* History Hook Button (Modern vertical button on left) */}
        {isFullScreen && !isHistoryOpen && (
          <button
            onClick={() => setIsHistoryOpen(true)}
            className="absolute left-0 top-1/2 -translate-y-1/2 z-[500] group flex items-center"
          >
            <div className="bg-white dark:bg-dark-surface border border-l-0 border-gray-100 dark:border-dark-border py-8 px-1 rounded-r-2xl shadow-xl hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-all flex flex-col items-center gap-4">
               <History className="w-4 h-4 text-indigo-600 dark:text-indigo-400 group-hover:scale-110 transition-transform" />
               <div className="[writing-mode:vertical-lr] rotate-180 text-[9px] font-black uppercase tracking-[0.3em] text-gray-400 dark:text-dark-muted group-hover:text-indigo-600 transition-colors">
                 History
               </div>
               <ChevronRight className="w-3 h-3 text-gray-300 dark:text-dark-muted" />
            </div>
          </button>
        )}

        <ChatHistoryOverlay 
          isOpen={isHistoryOpen}
          onClose={() => setIsHistoryOpen(false)}
          sessions={sessions}
          currentSessionId={currentSessionId}
          onLoadSession={loadSession}
          onDeleteSession={handleDeleteSession}
          onStartNewChat={startNewChat}
          isFullScreen={isFullScreen}
        />

        {/* Main Area */}
        <div className="flex-1 flex flex-col min-w-0 min-h-0">
          
          {/* Chat Messages */}
          <div className={`flex-1 overflow-y-auto custom-scrollbar min-h-0 ${isFullScreen ? 'bg-gray-50 dark:bg-dark-bg' : ''}`}>
            {activeTab === 'chat' && (
              <div className={`space-y-6 md:space-y-8 py-6 md:py-10 pb-10 max-w-5xl mx-auto w-full px-4 md:px-6`}>
                  {messages.map((msg, i) => (
                    <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'} min-w-0`}>
                      <div className={`min-w-0 ${
                        isFullScreen
                          ? `flex items-start space-x-2 md:space-x-4 max-w-[95%] md:max-w-[90%] ${msg.role === 'user' ? 'flex-row-reverse space-x-reverse' : ''}`
                          : 'flex flex-col w-full'
                      }`}>
                          <div className={`flex-shrink-0 shadow-sm ${
                            isFullScreen ? 'p-2 md:p-2.5 rounded-xl md:rounded-2xl' : `p-1.5 rounded-lg mb-1.5 ${msg.role === 'user' ? 'self-end' : 'self-start'}`
                          } ${
                            msg.role === 'user'
                              ? 'bg-indigo-600 text-white'
                              : (isFullScreen ? 'bg-white dark:bg-dark-surface text-indigo-600 dark:text-indigo-400 border border-gray-100 dark:border-dark-border' : 'bg-gray-100 dark:bg-dark-bg text-indigo-600')
                          }`}>
                            {msg.role === 'user'
                              ? <User className={isFullScreen ? 'w-4 h-4 md:w-5 md:h-5' : 'w-3.5 h-3.5'} />
                              : <Bot className={isFullScreen ? 'w-4 h-4 md:w-5 md:h-5' : 'w-3.5 h-3.5'} />}
                          </div>
                          <div className={`p-4 md:p-6 rounded-2xl md:rounded-3xl text-sm md:text-[15px] leading-relaxed shadow-lg ${
                            msg.role === 'user'
                              ? 'bg-indigo-600 text-white rounded-tr-none'
                              : (isFullScreen 
                                  ? 'bg-white dark:bg-dark-surface text-gray-800 dark:text-dark-text border border-gray-100 dark:border-dark-border rounded-tl-none prose dark:prose-invert max-w-none' 
                                  : 'bg-white dark:bg-dark-bg/50 text-gray-800 dark:text-dark-text border border-gray-100 dark:border-dark-border rounded-tl-none prose dark:prose-invert max-w-none')
                          }`}>
                            {msg.role === 'user' && msg.images && msg.images.length > 0 && (
                              <ChatMessageImages images={msg.images} variant="user" />
                            )}
                            {msg.content ? (
                              <div className="overflow-x-auto custom-scrollbar pb-1">
                                <ReactMarkdown 
                                  remarkPlugins={[remarkGfm]}
                                  components={{
                                    table: ({node, ...props}) => (
                                      <div className="overflow-x-auto my-4 rounded-xl border border-gray-100 dark:border-white/5">
                                        <table {...props} className="min-w-full divide-y divide-gray-200 dark:divide-white/10" />
                                      </div>
                                    ),
                                    a: ({ node, ...props }) => {
                                      const href = (props.href || '').toLowerCase();
                                      if (href.startsWith('citation://') || href.includes('citation:')) {
                                        const ref = href.split('://').pop() || '';
                                        return (
                                          <span className="inline-block translate-y-[2px]">
                                            <CitationButton reference={ref} toolCalls={msg.toolCalls || []} />
                                          </span>
                                        );
                                      }

                                      // Intercept API URLs and convert them to UI URLs
                                      const isApiUrl = href.includes('/api/v1/');
                                      if (isApiUrl) {
                                          const parts = props.href!.split('/');
                                          const id = parts[parts.length - 1];
                                          let uiPath = '';
                                          
                                          if (href.includes('/fhir/medication/')) uiPath = `/medications/details/${id}`;
                                          else if (href.includes('/biomarkers/')) uiPath = `/biomarkers/details/${id}`;
                                          else if (href.includes('/fhir/observation/')) uiPath = `/biomarkers`;
                                          
                                          if (uiPath) {
                                            return (
                                              <a 
                                                {...props} 
                                                href={uiPath}
                                                onClick={(e) => { e.preventDefault(); navigate(uiPath); }}
                                                className="text-indigo-600 hover:underline"
                                              >
                                                {props.children}
                                              </a>
                                            );
                                          }
                                      }
                                      
                                      return <a {...props} className="text-indigo-600 hover:underline" target="_blank" rel="noopener noreferrer">{props.children}</a>;
                                    },
                                    p: ({ node, ...props }) => {
                                      return <div {...props} className="mb-4 last:mb-0" />;
                                    },
                                    code: ({ node, className, children, ...props }: any) => {
                                      const inline = !className?.includes('language-');
                                      const content = String(children).replace(/\s/g, '');
                                      const toolCall = msg.toolCalls?.find(tc => tc.name.toLowerCase() === content.toLowerCase());
                                      if (inline && toolCall) {
                                        return <CitationButton reference={toolCall.name} toolCalls={msg.toolCalls || []} />;
                                      }
                                      return <code {...props} className={className}>{children}</code>;
                                    }
                                  }}
                                  urlTransform={(url) => url}
                                  skipHtml={false}
                                >
                                  {(() => {
                                    let text = msg.content;
                                    text = text.replace(/\[(?:Ref:\s*)?([a-z_]+)=([a-z0-9-_]+)(\.\.\.)?\]/gi, (match, type, uuid, truncated) => {
                                      return `@@@REF:${type}=${uuid}${truncated ? '...' : ''}@@@`;
                                    });
                                    text = text.replace(/\[(?:Ref:\s*)?([a-z_0-9]+)\]/gi, (match, name) => {
                                      if (name.includes('@@@REF:')) return match;
                                      return `@@@REF:${name}@@@`;
                                    });
                                    text = text.replace(/(^|\s)(get_[a-z_0-9]+)(?=\s|$)/gi, (match, space, name) => {
                                      return `${space}@@@REF:${name}@@@`;
                                    });
                                    text = text.replace(/@@@REF:([^@]+)@@@/g, (match, ref) => {
                                      const label = ref.split('=')[0].replace(/get_|recent_|history|_details/g, '').replace(/_/g, ' ');
                                      return `[${label}](citation://${ref})`;
                                    });
                                    return text;
                                  })()}
                                </ReactMarkdown>
                              </div>
                            ) : (
                              <div className="flex items-center space-x-1.5 py-2 px-1">
                                <style>{`
                                  @keyframes jump {
                                    0%, 100% { transform: translateY(0); opacity: 0.4; }
                                    50% { transform: translateY(-8px); opacity: 1; }
                                  }
                                `}</style>
                                <div className="w-1.5 h-1.5 bg-indigo-500 rounded-full" style={{ animation: 'jump 1s infinite -0.32s' }}></div>
                                <div className="w-1.5 h-1.5 bg-indigo-500 rounded-full" style={{ animation: 'jump 1s infinite -0.16s' }}></div>
                                <div className="w-1.5 h-1.5 bg-indigo-500 rounded-full" style={{ animation: 'jump 1s infinite' }}></div>
                              </div>
                            )}
                            
                            {msg.role === 'assistant' && (msg.toolCalls && msg.toolCalls.length > 0) && (
                              <div className="mt-5 flex flex-wrap gap-2 items-center">
                                {Array.from(new Set(msg.toolCalls.map(tc => tc.name))).map((name, idx) => {
                                    const tc = msg.toolCalls?.find(t => t.name === name);
                                    if (!tc) return null;
                                    return (
                                      <button 
                                        key={`tc-${idx}`} 
                                        onClick={() => tc.result && setInspectingTool(tc)}
                                        disabled={!tc.result}
                                        className={`flex items-center space-x-2 px-3 py-1.5 rounded-xl border transition-all ${
                                          tc.status === 'finished' 
                                            ? (isFullScreen ? 'bg-gray-50 dark:bg-dark-bg border-gray-200 dark:border-dark-border text-indigo-600 dark:text-indigo-400 hover:bg-white dark:hover:bg-dark-surface' : 'bg-white dark:bg-dark-surface border-indigo-100/30 dark:border-indigo-900/20 text-indigo-500 hover:bg-indigo-50 shadow-sm') 
                                            : 'bg-gray-50 dark:bg-dark-bg border-gray-100 dark:border-dark-border text-gray-400'
                                        }`}
                                      >
                                        <Database className={`w-3 h-3 ${tc.status !== 'finished' ? 'animate-pulse' : ''}`} />
                                        <span className="text-[10px] font-black uppercase tracking-widest">{tc.name.replace(/get_recent_|get_|history/g, '').replace(/_/g, ' ')}</span>
                                        {tc.status !== 'finished' && <Loader2 className="w-3 h-3 animate-spin ml-1" />}
                                      </button>
                                    );
                                })}
                              </div>
                            )}

                            {msg.role === 'assistant' && msg.tasks && msg.tasks.length > 0 && (
                              <div className="not-prose min-w-0">
                                {msg.tasks.map(task => (
                                  <HitlTaskCard
                                    key={task.proposal_id}
                                    task={task}
                                    sessionId={currentSessionId}
                                    onResolved={(updated) => {
                                      const messageKey = `msg-${i}`;
                                      // Compute the post-update task list synchronously
                                      // so we can decide whether to trigger a resume.
                                      const currentTasks = msg.tasks || [];
                                      const nextTasks = currentTasks.map(t =>
                                        t.proposal_id === task.proposal_id ? updated : t
                                      );
                                      const allTerminal =
                                        nextTasks.length > 0 &&
                                        nextTasks.every(t =>
                                          TERMINAL_HITL_STATUSES.has(t.status)
                                        );
                                      setMessages(prev => prev.map((m, idx) => {
                                        if (idx !== i || !m.tasks) return m;
                                        return {
                                          ...m,
                                          tasks: m.tasks.map(t => t.proposal_id === task.proposal_id ? updated : t),
                                        };
                                      }));
                                      // Auto-resume fires exactly once per message
                                      // when the last pending task transitions to
                                      // a terminal state.
                                      if (
                                        allTerminal &&
                                        !resumedMessageIds.current.has(messageKey) &&
                                        !msg._loadedFromHistory
                                      ) {
                                        triggerResume(i, messageKey);
                                      }
                                    }}
                                  />
                                ))}
                                {(() => {
                                  // "Continue" button for partial resolutions:
                                  // shown when at least one task is resolved AND
                                  // at least one is still pending. When ALL are
                                  // resolved, auto-resume handles it (no button).
                                  const tasks = msg.tasks || [];
                                  const hasTerminal = tasks.some(t => TERMINAL_HITL_STATUSES.has(t.status));
                                  const pendingCount = tasks.filter(t => !TERMINAL_HITL_STATUSES.has(t.status)).length;
                                  const isPartial = hasTerminal && pendingCount > 0;
                                  const alreadyResumed = resumedMessageIds.current.has(`msg-${i}`);
                                  if (!isPartial || alreadyResumed || msg._loadedFromHistory) return null;
                                  return (
                                    <div className="mt-3 flex justify-end">
                                      <button
                                        type="button"
                                        onClick={() => triggerResume(i, `msg-${i}`)}
                                        disabled={loading}
                                        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-[11px] font-black text-white bg-indigo-600 hover:bg-indigo-700 shadow-lg shadow-indigo-500/20 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
                                      >
                                        <span>
                                          {t('ai_chat.hitl.continue_partial', {
                                            defaultValue: 'Continue',
                                            count: pendingCount,
                                          })}
                                        </span>
                                        {pendingCount > 0 && (
                                          <span className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-white/20 text-[9px] font-black">
                                            {pendingCount}
                                          </span>
                                        )}
                                        <ChevronRight className="w-3.5 h-3.5" />
                                      </button>
                                    </div>
                                  );
                                })()}
                              </div>
                            )}
                          </div>
                      </div>
                    </div>
                  ))}
                  <div ref={messagesEndRef} />
              </div>
            )}

            {activeTab === 'insights' && (
              <div className="max-w-4xl mx-auto w-full p-4 md:p-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                  <div className={`p-6 md:p-8 rounded-2xl md:rounded-[2rem] border shadow-2xl ${
                    isFullScreen 
                      ? 'bg-amber-50 dark:bg-amber-900/10 border-amber-100 dark:border-amber-900/30 text-amber-900 dark:text-amber-200' 
                      : 'bg-amber-50 dark:bg-amber-900/10 border-amber-100 dark:border-amber-900/30 text-amber-900 dark:text-amber-200'
                  }`}>
                    <div className="flex items-center gap-3 md:gap-4 mb-4">
                       <div className="p-2 md:p-2.5 bg-amber-500 text-white rounded-xl shadow-lg shadow-amber-500/20">
                          <BarChart2 className="w-5 h-5 md:w-5.5 md:h-5.5" />
                       </div>
                       <h3 className="text-lg md:text-xl font-black uppercase tracking-[0.2em]">{t('ai_chat.insights.title')}</h3>
                    </div>
                    <p className="text-base md:text-lg font-medium leading-relaxed opacity-80">
                        {currentPatient 
                          ? t('ai_chat.insights.select_document', { name: `${currentPatient.name?.given?.join(' ')} ${currentPatient.name?.family}` })
                          : t('ai_chat.insights.select_patient')}
                    </p>
                  </div>
              </div>
            )}

            {activeTab === 'actions' && (
              <div className="max-w-4xl mx-auto w-full p-4 md:p-6 space-y-4 md:space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                  <h3 className="text-[10px] md:text-xs font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.4em] mb-4 md:mb-6 px-4 text-center">{t('ai_chat.actions.title')}</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
                    {CLINICAL_WORKFLOWS.map((action) => (
                      <button 
                        key={action.id} 
                        onClick={() => handleExecuteAction(action)}
                        className={`group p-6 md:p-6 flex flex-col items-start gap-3 md:gap-4 rounded-2xl md:rounded-[2rem] border transition-all text-left hover:scale-[1.02] hover:shadow-2xl active:scale-[0.98] ${
                          isFullScreen 
                            ? 'bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border hover:border-indigo-300 dark:hover:border-indigo-500' 
                            : 'bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border hover:border-indigo-300 dark:hover:border-indigo-500'
                        }`}
                      >
                        <div className={`w-10 h-10 md:w-10 md:h-10 ${action.color} text-white rounded-xl md:rounded-2xl flex items-center justify-center shadow-lg group-hover:rotate-12 transition-transform`}>
                           <action.icon className="w-5 h-5 md:w-5.5 md:h-5.5" />
                        </div>
                        <div>
                          <p className={`text-base md:text-lg font-black uppercase tracking-tight text-gray-900 dark:text-white`}>
                            {t(`ai_chat.actions.workflows.${action.id.replace(/-/g, '_')}.label`, { defaultValue: action.label })}
                          </p>
                          <p className={`text-xs md:text-sm font-medium text-gray-500 dark:text-dark-muted opacity-50`}>
                            {t(`ai_chat.actions.workflows.${action.id.replace(/-/g, '_')}.description`, { defaultValue: action.description })}
                          </p>
                        </div>
                      </button>
                    ))}
                  </div>
              </div>
            )}
          </div>

          {/* Input Bar */}
          {activeTab === 'chat' && (
            <div className={`shrink-0 p-4 md:p-6 lg:p-8 border-t ${isFullScreen ? 'bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border' : 'bg-white dark:bg-dark-surface border-gray-50 dark:border-dark-border'}`}>
              <form
                onSubmit={handleSendMessage}
                className="relative max-w-4xl mx-auto w-full group"
                onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setIsDragging(true); }}
                onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); setIsDragging(false); }}
                onDrop={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setIsDragging(false);
                  if (e.dataTransfer.files?.length) {
                    handleFilesAdded(e.dataTransfer.files);
                  }
                }}
              >
                  <div className={`absolute -inset-1 bg-gradient-to-r from-indigo-500 to-blue-500 rounded-[2.5rem] opacity-0 group-focus-within:opacity-30 blur transition-opacity duration-500 pointer-events-none`} />

                  {/* Preview rail lives OUTSIDE the rounded input box so
                      thumbnails keep their own corners and aren't clipped by
                      the box's overflow-hidden + radius. */}
                  <ChatAttachmentPreviewRail
                    attachments={pendingAttachments}
                    onRemove={(id) => setPendingAttachments(prev => prev.filter(a => a.id !== id))}
                  />

                  <div className={`relative flex items-center border rounded-[1.8rem] md:rounded-[2.2rem] transition-all shadow-2xl overflow-hidden ${
                    isDragging ? 'ring-4 ring-indigo-500/40 border-indigo-400' : ''
                  } ${
                    isFullScreen 
                      ? 'bg-gray-50 dark:bg-dark-bg border-gray-200 dark:border-dark-border focus-within:ring-4 focus-within:ring-indigo-500/20' 
                      : 'bg-white dark:bg-dark-surface border-gray-200 dark:border-dark-border focus-within:ring-4 focus-within:ring-indigo-600/20'
                  }`}>
                    <div className="shrink-0 pl-3 md:pl-5">
                      <ChatAttachmentPicker
                        attachments={pendingAttachments}
                        onChange={setPendingAttachments}
                        onToast={(m) => toast.warning(m)}
                        disabled={loading}
                      />
                    </div>
                    <textarea
                      ref={textareaRef}
                      rows={1}
                      className="flex-1 pl-2 md:pl-3 pr-2 py-4 md:py-5 bg-transparent text-sm font-medium text-gray-900 dark:text-dark-text placeholder-gray-400 dark:placeholder-slate-500 resize-none outline-none border-none focus:ring-0 focus:outline-none focus-visible:ring-0 overflow-y-auto custom-scrollbar leading-relaxed shadow-none"
                      placeholder={t('ai_chat.input.placeholder')}
                      value={userInput}
                      onChange={e => setUserInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          handleSendMessage();
                        }
                      }}
                      onPaste={(e) => {
                        const files = Array.from(e.clipboardData.items)
                          .map(item => item.getAsFile())
                          .filter((f): f is File => !!f && f.type.startsWith('image/'));
                        if (files.length) {
                          e.preventDefault();
                          const dt = new DataTransfer();
                          files.forEach(f => dt.items.add(f));
                          handleFilesAdded(dt.files);
                        }
                      }}
                      style={{ 
                        minHeight: '60px', 
                        maxHeight: '200px',
                        boxShadow: 'none',
                        outline: 'none',
                        border: 'none'
                      }}
                    />
                    <div className="pr-2 md:pr-3">
                      <button
                        type="submit"
                        disabled={loading || (!userInput.trim() && pendingAttachments.filter(a => a.status === 'ready').length === 0)}
                        className="p-2.5 md:p-3 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white rounded-full transition-all shadow-lg hover:scale-105 active:scale-95 flex items-center justify-center"
                      >
                        {loading ? <Loader2 className="w-4 h-4 md:w-5 md:h-5 animate-spin" /> : <Send className="w-4 h-4 md:w-5 md:h-5" />}
                      </button>
                    </div>
                  </div>
                  {isDragging && (
                    <div className="absolute inset-0 z-10 bg-indigo-50/90 dark:bg-indigo-900/40 flex items-center justify-center pointer-events-none rounded-[1.8rem] md:rounded-[2.2rem]">
                      <span className="text-sm font-black uppercase tracking-widest text-indigo-600 dark:text-indigo-300">
                        {t('ai_chat.attachments.drop', { defaultValue: 'Drop images to attach' })}
                      </span>
                    </div>
                  )}
              </form>
              <p className="text-[8px] md:text-[10px] text-gray-500 dark:text-slate-500 text-center mt-3 md:mt-5 font-black uppercase tracking-[0.2em] opacity-40">{t('ai_chat.input.disclaimer')}</p>
            </div>
          )}
        </div>

        {/* Overlays */}
        <ChatLedgerOverlay 
          isOpen={isLedgerOpen}
          onClose={() => setIsLedgerOpen(false)}
          messages={messages}
          onInspectTool={setInspectingTool}
          isFullScreen={isFullScreen}
        />

        <AIToolsModal
          isOpen={isToolsModalOpen}
          onClose={() => setIsToolsModalOpen(false)}
          patientId={currentPatient?.id || ''}
          examinationId={currentExaminationId || undefined}
        />
      </div>

      {/* Inspector (Global) */}
      {inspectingTool && (
        <ChatInspector 
          tool={inspectingTool}
          onClose={() => setInspectingTool(null)}
          viewMode={inspectorViewMode}
          onViewModeChange={setInspectorViewMode}
        />
      )}
    </div>
  );
};
