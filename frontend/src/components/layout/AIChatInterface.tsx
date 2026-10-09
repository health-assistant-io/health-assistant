import React, { useCallback, useContext, useEffect, useRef, useState } from 'react';
import {
  X,
  MessageSquare,
  Sparkles,
  BarChart2,
  Database,
  ChevronRight,
  History,
  Plus,
  Maximize2,
  Minimize2,
  Wrench,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import {
  ChatComposer,
  ChatMessage,
  ChatPanel,
  ChatToolCard,
  ChatTranscript,
  ChatTurnStatus,
  MarkdownSurface,
  type ChatMessageView,
  type MarkdownComponents,
} from '../ui/chat';
import { DictationButton, DictationStrip, useDictation } from '../ui/dictation';
import {
  HealthChatContext,
  HealthChatProvider,
  useHealthChatContext,
  type ChatRow,
} from '../chat/useHealthChat';
import { SessionList } from '../chat/SessionList';
import { toolChipTitle } from '../chat/chatTransport';
import {
  chatUrlTransform,
  citationsToMarkdown,
  citationRef,
  isCitationHref,
  rewriteApiLink,
} from '../chat/markdownLinks';
import { classifyDictationError } from '../chat/dictationErrors';
import { transcribeAudio } from '../../services/aiAssistanceService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { useUIStore } from '../../store/slices/uiSlice';
import { CLINICAL_WORKFLOWS, type ClinicalAction } from '../../config/clinicalWorkflows';
import { useIsMobile } from '../../hooks/useMediaQuery';
import { toast } from 'react-toastify';
import type { ToolCallInfo } from '../../types/ai';
import { CitationButton } from '../ai/CitationButton';
import { ChatInspector } from '../ai/ChatInspector';
import { ChatLedgerOverlay } from '../ai/ChatLedgerOverlay';
import { AIToolsModal } from '../ai/AIToolsModal';
import { HitlTaskCard } from '../ai/hitl/HitlTaskCard';
import { TERMINAL_HITL_STATUSES } from '../ai/hitl/registry';
import { ActiveTaskBadge } from '../ui/ActiveTaskBadge';
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

const NODE_LABEL_KEYS: Record<string, string> = {
  agent_step: 'ai_chat.flow.think',
  tool_exec: 'ai_chat.flow.tools',
  await_user: 'ai_chat.flow.await',
  finalize: 'ai_chat.flow.finalize',
};

function toolStatus(status: ToolCallInfo['status']): 'running' | 'done' {
  return status === 'finished' ? 'done' : 'running';
}

function uniqueToolCalls(toolCalls: ToolCallInfo[]): ToolCallInfo[] {
  return Array.from(new Map(toolCalls.map((tc) => [tc.name, tc])).values());
}

export const AIChatInterface: React.FC<Props> = (props) => {
  const getContext = useCallback(
    () => ({ current_tab: props.activeTab ?? 'chat' }),
    [props.activeTab],
  );
  const content = <AIChatInterfaceInner {...props} />;
  const existing = useContext(HealthChatContext);
  if (existing !== null) {
    // A hosting surface (the /ai-assistant page) already owns the provider
    // so its session-list aside shares this interface's chat state.
    return content;
  }
  return (
    <HealthChatProvider
      isFullScreen={props.isFullScreen ?? false}
      initialSessionId={props.initialSessionId}
      getContext={getContext}
    >
      {content}
    </HealthChatProvider>
  );
};

const AIChatInterfaceInner: React.FC<Props> = ({
  isFullScreen = false,
  onClose,
  hideHeader = false,
  activeTab: externalTab,
  onTabChange,
  interfaceRef,
}) => {
  const chat = useHealthChatContext();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [internalTab, setInternalTab] = useState<'chat' | 'insights' | 'actions'>('chat');
  const [inspectingTool, setInspectingTool] = useState<ToolCallInfo | null>(null);
  const [inspectorViewMode, setInspectorViewMode] = useState<'raw' | 'table'>('table');
  const [isLedgerOpen, setIsLedgerOpen] = useState(false);
  const [isToolsModalOpen, setIsToolsModalOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const activeTab = externalTab || internalTab;
  const setActiveTab = (tab: 'chat' | 'insights' | 'actions') => {
    if (onTabChange) onTabChange(tab);
    else setInternalTab(tab);
  };

  const currentPatient = usePatientStore((state) => state.currentPatient);
  const currentExaminationId = useUIStore((state) => state.currentExaminationId);
  const lastNonAiPath = useUIStore((state) => state.lastNonAiPath);
  const setAIDrawerOpen = useUIStore((state) => state.setAIDrawerOpen);

  const uniqueSourcesCount = Array.from(
    new Set(
      chat.rows.flatMap((m) => m.toolCalls || []).filter((tc) => tc.status === 'finished').map((tc) => tc.name),
    ),
  ).length;

  React.useImperativeHandle(interfaceRef, () => ({
    startNewChat: () => {
      chat.startNewChat();
      setIsLedgerOpen(false);
      setIsToolsModalOpen(false);
    },
    toggleHistory: () => chat.setIsHistoryOpen((prev) => !prev),
    toggleLedger: () => setIsLedgerOpen((prev) => !prev),
    toggleToolsModal: () => setIsToolsModalOpen((prev) => !prev),
  }));

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        chat.setIsHistoryOpen(false);
        setIsLedgerOpen(false);
        setInspectingTool(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [chat]);

  const handleExecuteAction = (action: ClinicalAction) => {
    setActiveTab('chat');
    const localizedPrompt = t(`ai_chat.actions.workflows.${action.id.replace(/-/g, '_')}.prompt`, {
      defaultValue: action.prompt,
    });
    void chat.submit(localizedPrompt);
  };

  const markdownComponents = (toolCalls: ToolCallInfo[]): MarkdownComponents => ({
    a: ({ node: _node, href, children, ...props }) => {
      if (isCitationHref(href)) {
        return (
          <span className="inline-block translate-y-[2px]">
            <CitationButton reference={citationRef(href ?? '')} toolCalls={toolCalls} />
          </span>
        );
      }
      const rewrite = href ? rewriteApiLink(href) : null;
      if (rewrite) {
        return (
          <a
            {...props}
            href={rewrite.uiPath}
            onClick={(e) => {
              e.preventDefault();
              navigate(rewrite.uiPath);
            }}
            className="text-indigo-600 hover:underline"
          >
            {children}
          </a>
        );
      }
      return (
        <a {...props} className="text-indigo-600 hover:underline" target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      );
    },
    code: ({ node: _node, className, children, ...props }) => {
      const inline = !className?.includes('language-');
      const content = String(children).replace(/\s/g, '');
      const toolCall = toolCalls.find((tc) => tc.name.toLowerCase() === content.toLowerCase());
      if (inline && toolCall) {
        return <CitationButton reference={toolCall.name} toolCalls={toolCalls} />;
      }
      return (
        <code {...props} className={className}>
          {children}
        </code>
      );
    },
  });

  const liveToolInfos: ToolCallInfo[] = chat.stream.toolCalls.map((call) => ({
    name: call.name,
    args: call.args,
    result: call.result,
    status: call.status === 'done' ? 'finished' : 'executing',
  }));

  const turnStatusLabel = (() => {
    const running = [...chat.stream.nodes].reverse().find((n) => n.status === 'running');
    const node = running ?? chat.stream.nodes[chat.stream.nodes.length - 1];
    if (node && NODE_LABEL_KEYS[node.id]) return t(NODE_LABEL_KEYS[node.id]);
    return node?.label ?? node?.id ?? t('ai_chat.flow.think');
  })();

  const toolCardLabels = {
    running: t('ai_chat.flow.tools_running'),
    done: t('ai_chat.flow.tools_done'),
    failed: t('ai_chat.flow.tools_failed'),
    args: t('ai_chat.inspector.args'),
    result: t('ai_chat.inspector.result'),
  };

  const renderToolCards = (toolCalls: ToolCallInfo[] | undefined) => {
    const unique = uniqueToolCalls(toolCalls ?? []);
    if (unique.length === 0) return null;
    return (
      <div className="not-prose mt-4 flex w-full flex-col gap-1.5">
        {unique.map((tc) => (
          <ChatToolCard
            key={tc.name}
            name={tc.name}
            title={toolChipTitle(tc.name)}
            status={toolStatus(tc.status)}
            args={tc.args}
            result={tc.result}
            icon={Database}
            labels={toolCardLabels}
          />
        ))}
      </div>
    );
  };

  const renderTasks = (row: ChatRow | null) => {
    const tasks = row ? row.tasks : chat.liveExtras.tasks;
    if (!tasks || tasks.length === 0) return null;
    return (
      <div className="not-prose min-w-0">
        {tasks.map((task) => (
          <HitlTaskCard
            key={task.proposal_id}
            task={task}
            sessionId={chat.currentSessionId}
            onResolved={(updated) => chat.resolveTask(row, task.proposal_id, updated)}
          />
        ))}
        {row
          ? (() => {
              // "Continue" button for partial resolutions: shown when at
              // least one task is resolved AND at least one is still
              // pending. When ALL are resolved, auto-resume handles it.
              const hasTerminal = tasks.some((x) => TERMINAL_HITL_STATUSES.has(x.status));
              const pendingCount = tasks.filter((x) => !TERMINAL_HITL_STATUSES.has(x.status)).length;
              const isPartial = hasTerminal && pendingCount > 0;
              const alreadyResumed = chat.resumedRowIds.current.has(row.id);
              if (!isPartial || alreadyResumed || row.fromHistory) return null;
              return (
                <div className="mt-3 flex justify-end">
                  <button
                    type="button"
                    onClick={() => void chat.triggerResume(row.id)}
                    disabled={chat.sending}
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
            })()
          : null}
      </div>
    );
  };

  const renderMessage = (view: ChatMessageView) => {
    const row = view.meta?.row as ChatRow;
    return (
      <ChatMessage
        key={view.id}
        role={view.role}
        compact={!isFullScreen}
        content={
          <MarkdownSurface
            value={citationsToMarkdown(row.content)}
            components={markdownComponents(row.toolCalls ?? [])}
            urlTransform={chatUrlTransform}
          />
        }
        attachments={
          row.role === 'user' && row.images && row.images.length > 0 ? (
            <ChatMessageImages images={row.images} variant="user" />
          ) : undefined
        }
        actions={{
          onCopy: () => {
            void navigator.clipboard?.writeText(row.content).catch(() => undefined);
          },
        }}
        labels={{ copy: t('ai_chat.tooltips.copy') }}
      >
        {row.role === 'assistant' ? (
          <>
            {renderToolCards(row.toolCalls)}
            {renderTasks(row)}
          </>
        ) : null}
      </ChatMessage>
    );
  };

  const stream = chat.stream;
  const liveTail =
    stream.live !== null ? (
      <ChatMessage
        role="assistant"
        compact={!isFullScreen}
        status={
          stream.status === 'interrupted'
            ? 'interrupted'
            : stream.status === 'error'
              ? 'error'
              : 'streaming'
        }
        error={stream.error ?? undefined}
        content={
          <>
            {renderToolCards(liveToolInfos)}
            {stream.text !== null ? (
              <MarkdownSurface
                value={citationsToMarkdown(stream.text)}
                streaming
                components={markdownComponents(liveToolInfos)}
                urlTransform={chatUrlTransform}
              />
            ) : stream.status === 'error' ? null : (
              <ChatTurnStatus
                variant="row"
                label={turnStatusLabel}
                startedAt={stream.startedAt ?? undefined}
                labels={{ timer: t('ai_chat.flow.elapsed') }}
              />
            )}
          </>
        }
      >
        {renderTasks(null)}
      </ChatMessage>
    ) : undefined;

  const emptyState = (
    <div className="flex justify-start py-2">
      <ChatMessage
        role="assistant"
        compact={!isFullScreen}
        content={<MarkdownSurface value={t('ai_chat.welcome_message')} />}
      />
    </div>
  );

  const tabs = [
    { id: 'chat', icon: MessageSquare, label: t('ai_chat.tabs.chat') },
    { id: 'insights', icon: BarChart2, label: t('ai_chat.tabs.insights') },
    { id: 'actions', icon: Sparkles, label: t('ai_chat.tabs.actions') },
  ] as const;

  return (
    <div
      className={`flex-1 flex flex-col bg-white dark:bg-dark-bg min-h-0 ${
        isFullScreen ? '' : 'rounded-none shadow-[-20px_0_50px_rgba(0,0,0,0.1)] border-l border-gray-100 dark:border-dark-border'
      }`}
    >
      {!hideHeader && (
        <div className={`flex flex-col shrink-0 ${isFullScreen ? 'bg-white dark:bg-dark-surface text-gray-900 dark:text-white' : ''}`}>
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

            {isFullScreen && (
              <div className="hidden sm:flex items-center bg-gray-100 dark:bg-dark-bg p-1 rounded-2xl border border-gray-200 dark:border-dark-border mx-2 md:mx-4">
                {tabs.map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
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

            <div className="flex items-center space-x-0.5 md:space-x-1">
              <ActiveTaskBadge taskType="chat" className="mr-2 md:mr-3" variant={isFullScreen ? 'default' : 'white'} />

              {isFullScreen && (
                <button
                  onClick={() => chat.setIsHistoryOpen((prev) => !prev)}
                  className={`lg:hidden p-1.5 md:p-2 rounded-lg md:rounded-xl transition-all ${
                    chat.isHistoryOpen
                      ? 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400'
                      : 'text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-dark-muted dark:hover:text-dark-text dark:hover:bg-dark-bg'
                  }`}
                  title={t('ai_chat.tooltips.history')}
                >
                  <History className="w-4 h-4 md:w-5 md:h-5" />
                </button>
              )}
              <button
                onClick={() => {
                  chat.startNewChat();
                  setIsLedgerOpen(false);
                  setIsToolsModalOpen(false);
                }}
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
                title={t('ai_chat.tooltips.agent_capabilities')}
              >
                <Wrench className="w-4 h-4 md:w-5 md:h-5" />
              </button>

              <button
                onClick={() => setIsLedgerOpen((prev) => !prev)}
                className={`relative p-1.5 md:p-2 rounded-lg md:rounded-xl transition-all ${
                  isLedgerOpen
                    ? 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-400'
                    : isFullScreen
                      ? 'text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:text-dark-muted dark:hover:text-dark-text dark:hover:bg-dark-bg'
                      : 'text-white/70 hover:text-white hover:bg-white/10'
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
                    setAIDrawerOpen(false);
                    navigate(
                      chat.currentSessionId ? `/ai-assistant/${chat.currentSessionId}` : '/ai-assistant',
                    );
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
                    setAIDrawerOpen(true);
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

          {isMobile && (
            <div className={`flex border-b border-gray-100 dark:border-dark-border bg-gray-50/50 dark:bg-dark-bg/50 px-2 shrink-0 ${isFullScreen ? 'sm:hidden' : ''}`}>
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
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
        {isFullScreen && !chat.isHistoryOpen && (
          <button
            onClick={() => chat.setIsHistoryOpen(true)}
            className="lg:hidden absolute left-0 top-1/2 -translate-y-1/2 z-960 group flex items-center"
          >
            <div className="bg-white dark:bg-dark-surface border border-l-0 border-gray-100 dark:border-dark-border py-8 px-1 rounded-r-2xl shadow-xl hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-all flex flex-col items-center gap-4">
              <History className="w-4 h-4 text-indigo-600 dark:text-indigo-400 group-hover:scale-110 transition-transform" />
              <div className="[writing-mode:vertical-lr] rotate-180 text-[9px] font-black uppercase tracking-[0.3em] text-gray-400 dark:text-dark-muted group-hover:text-indigo-600 transition-colors">
                {t('ai_chat.history.title')}
              </div>
              <ChevronRight className="w-3 h-3 text-gray-300 dark:text-dark-muted" />
            </div>
          </button>
        )}

        {isFullScreen && chat.isHistoryOpen && (
          <>
            <div
              className="lg:hidden fixed inset-0 bg-black/40 backdrop-blur-xs z-1040 animate-in fade-in duration-500 cursor-pointer"
              onClick={() => chat.setIsHistoryOpen(false)}
            />
            <div className="lg:hidden fixed inset-y-0 left-0 z-1041 w-80 max-w-[85vw] bg-white dark:bg-dark-bg border-r border-gray-100 dark:border-dark-border shadow-2xl animate-in slide-in-from-right duration-300 flex flex-col p-2">
              <SessionList onPick={() => chat.setIsHistoryOpen(false)} />
            </div>
          </>
        )}

        <div className="flex-1 flex flex-col min-w-0 min-h-0">
          {activeTab === 'chat' && (
            <ChatPanel
              variant={isFullScreen ? 'page' : 'sidebar'}
              className="flex-1 min-h-0"
              transcript={
                <ChatTranscript
                  className="flex-1"
                  items={chat.viewMessages}
                  renderItem={renderMessage}
                  live={liveTail}
                  emptyState={emptyState}
                  labels={{
                    log: t('ai_chat.transcript.aria'),
                    scrollToBottom: t('ai_chat.transcript.scroll_down'),
                  }}
                />
              }
              composer={
                <ChatComposerBar textareaRef={textareaRef} />
              }
              footer={t('ai_chat.input.disclaimer')}
            />
          )}

          {activeTab === 'insights' && (
            <div className="flex-1 overflow-y-auto custom-scrollbar">
              <div className="max-w-4xl mx-auto w-full p-4 md:p-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                <div className="p-6 md:p-8 rounded-2xl md:rounded-4xl border shadow-2xl bg-amber-50 dark:bg-amber-900/10 border-amber-100 dark:border-amber-900/30 text-amber-900 dark:text-amber-200">
                  <div className="flex items-center gap-3 md:gap-4 mb-4">
                    <div className="p-2 md:p-2.5 bg-amber-500 text-white rounded-xl shadow-lg shadow-amber-500/20">
                      <BarChart2 className="w-5 h-5 md:w-5.5 md:h-5.5" />
                    </div>
                    <h3 className="text-lg md:text-xl font-black uppercase tracking-[0.2em]">{t('ai_chat.insights.title')}</h3>
                  </div>
                  <p className="text-base md:text-lg font-medium leading-relaxed opacity-80">
                    {currentPatient
                      ? t('ai_chat.insights.select_document', {
                          name: `${currentPatient.name?.given?.join(' ')} ${currentPatient.name?.family}`,
                        })
                      : t('ai_chat.insights.select_patient')}
                  </p>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'actions' && (
            <div className="flex-1 overflow-y-auto custom-scrollbar">
              <div className="max-w-4xl mx-auto w-full p-4 md:p-6 space-y-4 md:space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                <h3 className="text-[10px] md:text-xs font-black text-gray-400 dark:text-dark-muted uppercase tracking-[0.4em] mb-4 md:mb-6 px-4 text-center">{t('ai_chat.actions.title')}</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
                  {CLINICAL_WORKFLOWS.map((action) => (
                    <button
                      key={action.id}
                      onClick={() => handleExecuteAction(action)}
                      className="group p-6 md:p-6 flex flex-col items-start gap-3 md:gap-4 rounded-2xl md:rounded-4xl border transition-all text-left hover:scale-[1.02] hover:shadow-2xl active:scale-[0.98] bg-white dark:bg-dark-surface border-gray-100 dark:border-dark-border hover:border-indigo-300 dark:hover:border-indigo-500"
                    >
                      <div className={`w-10 h-10 md:w-10 md:h-10 ${action.color} text-white rounded-xl md:rounded-2xl flex items-center justify-center shadow-lg group-hover:rotate-12 transition-transform`}>
                        <action.icon className="w-5 h-5 md:w-5.5 md:h-5.5" />
                      </div>
                      <div>
                        <p className="text-base md:text-lg font-black uppercase tracking-tight text-gray-900 dark:text-white">
                          {t(`ai_chat.actions.workflows.${action.id.replace(/-/g, '_')}.label`, { defaultValue: action.label })}
                        </p>
                        <p className="text-xs md:text-sm font-medium text-gray-500 dark:text-dark-muted opacity-50">
                          {t(`ai_chat.actions.workflows.${action.id.replace(/-/g, '_')}.description`, { defaultValue: action.description })}
                        </p>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        <ChatLedgerOverlay
          isOpen={isLedgerOpen}
          onClose={() => setIsLedgerOpen(false)}
          messages={chat.rows}
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

const ChatComposerBar: React.FC<{ textareaRef: React.Ref<HTMLTextAreaElement> }> = ({
  textareaRef,
}) => {
  const chat = useHealthChatContext();
  const { t } = useTranslation();
  const dictation = useDictation({
    transcribe: (blob) => transcribeAudio(blob),
    classifyError: classifyDictationError,
    onResult: (text) => {
      const base = chat.draft;
      const glue = base !== '' && !base.endsWith(' ') ? ' ' : '';
      chat.setDraft(`${base}${glue}${text} `);
      const node =
        typeof textareaRef === 'object' && textareaRef !== null
          ? textareaRef.current
          : null;
      if (node) setTimeout(() => node.focus(), 0);
    },
  });
  const [micHidden, setMicHidden] = useState(false);
  const errorKind = dictation.error?.kind;
  useEffect(() => {
    if (errorKind === 'unassigned' || errorKind === 'unsupported') {
      setMicHidden(true);
      dictation.dismissError();
    }
  }, [errorKind, dictation]);

  const dictationStrip =
    dictation.status !== 'idle' || dictation.error !== null ? (
      <DictationStrip
        status={dictation.status}
        seconds={dictation.seconds}
        levelRef={dictation.levelRef}
        error={dictation.error}
        labels={{
          recording: t('ai_chat.voice.recording'),
          transcribing: t('ai_chat.voice.transcribing'),
          stop: t('ai_chat.voice.stop_label'),
          cancel: t('ai_chat.voice.cancel'),
          dismissError: t('ai_chat.voice.dismiss'),
          unsupported: t('ai_chat.voice.unsupported'),
          denied: t('ai_chat.voice.permission_denied'),
          unassigned: t('ai_chat.voice.unassigned'),
          failed: t('ai_chat.voice.failed', { detail: dictation.error?.detail ?? '' }),
        }}
        onStop={() => void dictation.stop()}
        onCancel={dictation.cancel}
        onDismissError={dictation.dismissError}
      />
    ) : undefined;

  return (
    <ChatComposer
      value={chat.draft}
      onValueChange={chat.setDraft}
      onSubmit={() => void chat.submit()}
      canSubmit={chat.draft.trim() !== '' || chat.pendingAttachments.some((a) => a.status === 'ready')}
      sending={chat.sending}
      placeholder={t('ai_chat.input.placeholder')}
      ariaLabel={t('ai_chat.input.placeholder')}
      labels={{
        send: t('ai_chat.tooltips.send'),
        stop: t('ai_chat.tooltips.stop'),
        dropFiles: t('ai_chat.attachments.drop'),
      }}
      textareaRef={textareaRef}
      toolbarStart={
        <>
          <ChatAttachmentPicker
            attachments={chat.pendingAttachments}
            onChange={chat.setPendingAttachments}
            onToast={(m) => toast.warning(m)}
            disabled={chat.sending}
          />
          {micHidden ? null : (
            <DictationButton
              status={dictation.status}
              onStart={() => void dictation.start()}
              label={t('ai_chat.voice.record')}
            />
          )}
        </>
      }
      attachments={
        chat.pendingAttachments.length > 0 ? (
          <ChatAttachmentPreviewRail
            attachments={chat.pendingAttachments}
            onRemove={(id) =>
              chat.setPendingAttachments((prev) => prev.filter((a) => a.id !== id))
            }
          />
        ) : undefined
      }
      suggestions={dictationStrip}
      onAttachFiles={chat.handleFilesAdded}
    />
  );
};
