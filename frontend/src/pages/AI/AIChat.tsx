import React, { useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { AIChatInterface, type AIChatHandlers } from '../../components/layout/AIChatInterface';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { Sparkles, MessageSquare, BarChart2, History, Plus, Database, Minimize2, Wrench } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useUIStore } from '../../store/slices/uiSlice';
import { AIBadge } from '../../components/ui/AIBadge';

const AIChatPage: React.FC = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<'chat' | 'insights' | 'actions'>('chat');
  const interfaceRef = useRef<AIChatHandlers>(null);
  
  const lastNonAiPath = useUIStore(state => state.lastNonAiPath);
  const setAIDrawerOpen = useUIStore(state => state.setAIDrawerOpen);

  return (
    <div className="flex-1 flex flex-col bg-white dark:bg-dark-bg animate-in fade-in duration-300 overflow-hidden min-h-0">
      <PageHeader
        title={t('ai_chat.header.title')}
        subtitle={t('ai_chat.header.subtitle')}
        icon={<Sparkles className="w-8 h-8 text-indigo-500" />}
        breadcrumbs={[]}
        showBackButton={true}
      />

      <StickyToolbar
        className="px-4 md:px-6 pt-4 mb-2"
        center={
          <div className="flex items-center bg-gray-100 dark:bg-dark-surface/50 p-1 rounded-2xl border border-gray-200 dark:border-dark-border shadow-sm">
            {[
              { id: 'chat', icon: MessageSquare, label: t('ai_chat.tabs.chat') },
              { id: 'insights', icon: BarChart2, label: t('ai_chat.tabs.insights') },
              { id: 'actions', icon: Sparkles, label: t('ai_chat.tabs.actions') }
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={`flex items-center gap-2 px-6 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all ${
                  activeTab === tab.id 
                    ? 'bg-indigo-600 text-white shadow-lg' 
                    : 'text-gray-500 hover:text-gray-700 dark:text-dark-muted dark:hover:text-dark-text'
                }`}
              >
                <tab.icon className="w-3.5 h-3.5" />
                <span className="hidden md:inline">{tab.label}</span>
              </button>
            ))}
          </div>
        }
        actions={
          <div className="flex items-center space-x-1 pl-4">
            <AIBadge taskType="chat" className="mr-3" />
            <button 
              onClick={() => interfaceRef.current?.toggleHistory()}
              className="p-2.5 text-gray-500 hover:text-indigo-600 dark:text-dark-muted dark:hover:text-dark-text rounded-xl hover:bg-indigo-50 dark:hover:bg-dark-surface transition-all"
              title={t('ai_chat.tooltips.history')}
            >
              <History className="w-5 h-5" />
            </button>
            <button 
              onClick={() => interfaceRef.current?.startNewChat()}
              className="p-2.5 text-gray-500 hover:text-indigo-600 dark:text-dark-muted dark:hover:text-dark-text rounded-xl hover:bg-indigo-50 dark:hover:bg-dark-surface transition-all"
              title={t('ai_chat.tooltips.new_chat')}
            >
              <Plus className="w-5 h-5" />
            </button>
            <button 
              onClick={() => interfaceRef.current?.toggleToolsModal()}
              className="p-2.5 text-gray-500 hover:text-indigo-600 dark:text-dark-muted dark:hover:text-dark-text rounded-xl hover:bg-indigo-50 dark:hover:bg-dark-surface transition-all"
              title="View Agent Capabilities"
            >
              <Wrench className="w-5 h-5" />
            </button>
            <button 
              onClick={() => interfaceRef.current?.toggleLedger()}
              className="p-2.5 text-gray-500 hover:text-indigo-600 dark:text-dark-muted dark:hover:text-dark-text rounded-xl hover:bg-indigo-50 dark:hover:bg-dark-surface transition-all"
              title={t('ai_chat.tooltips.ledger')}
            >
              <Database className="w-5 h-5" />
            </button>
            <button 
              onClick={() => {
                setAIDrawerOpen(true);
                navigate(lastNonAiPath || '/');
              }}
              className="p-2.5 text-gray-500 hover:text-indigo-600 dark:text-dark-muted dark:hover:text-dark-text rounded-xl hover:bg-indigo-50 dark:hover:bg-dark-surface transition-all"
              title={t('ai_chat.tooltips.minimize')}
            >
              <Minimize2 className="w-5 h-5" />
            </button>
          </div>
        }
      />
      <div className="flex-1 relative flex flex-col min-h-0">
        <AIChatInterface 
          isFullScreen={true} 
          initialSessionId={sessionId} 
          hideHeader={true} 
          activeTab={activeTab}
          onTabChange={setActiveTab}
          interfaceRef={interfaceRef}
        />
      </div>
    </div>
  );
};

export default AIChatPage;
