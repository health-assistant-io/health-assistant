import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { 
  Bell, 
  Clock, 
  Calendar, 
  Trash2, 
  Play, 
  CheckCircle2, 
  XCircle, 
  RefreshCw,
  Info,
  Smartphone,
  ShieldAlert
} from 'lucide-react';
import { notificationService, Notification, NotificationTrigger } from '../../services/notificationService';
import { usePatientStore } from '../../store/slices/patientSlice';
import { formatDistanceToNow, format } from 'date-fns';
import { el, enUS } from 'date-fns/locale';
import { PageHeader } from '../../components/ui/PageHeader';
import { StickyToolbar } from '../../components/ui/StickyToolbar';
import { NoPatientState } from '../../components/ui/NoPatientState';

export default function NotificationManagement() {
  const { t, i18n } = useTranslation();
  const { currentPatient } = usePatientStore();
  
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [triggers, setTriggers] = useState<NotificationTrigger[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<'history' | 'triggers'>('history');
  const [pushStatus, setPushStatus] = useState<string>('Checking...');

  const dateLocale = (i18n?.language === 'el') ? el : enUS;

  useEffect(() => {
    checkPushSubscription();
  }, []);

  useEffect(() => {
    if (currentPatient?.id) {
      loadData();
      
      const interval = setInterval(() => {
        loadData();
      }, 15000);
      
      return () => clearInterval(interval);
    }
  }, [currentPatient?.id]);

  const checkPushSubscription = async () => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      setPushStatus('Not Supported');
      return;
    }
    
    try {
      const registration = await navigator.serviceWorker.getRegistration();
      if (!registration) {
        setPushStatus('Not Registered');
        return;
      }
      const subscription = await registration.pushManager.getSubscription();
      setPushStatus(subscription ? 'Subscribed' : 'Not Subscribed');
    } catch (error) {
      setPushStatus('Error');
    }
  };

  const loadData = async () => {
    if (!currentPatient?.id) return;
    if (notifications.length === 0 && triggers.length === 0) {
      setLoading(true);
    }
    try {
      const [notifs, trigs] = await Promise.all([
        notificationService.getNotifications(currentPatient.id),
        notificationService.getTriggers(currentPatient.id)
      ]);
      console.log('Fetched Notifications:', notifs);
      console.log('Fetched Triggers:', trigs);
      setNotifications(Array.isArray(notifs) ? notifs : []);
      setTriggers(Array.isArray(trigs) ? trigs : []);
    } catch (error) {
      console.error('Failed to load notification management data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleTestTrigger = async (id: string) => {
    try {
      await notificationService.testTrigger(id);
      setTimeout(loadData, 2000);
    } catch (error) {
      alert('Failed to test trigger');
    }
  };

  const handleDeleteTrigger = async (id: string) => {
    if (!confirm('Are you sure you want to delete this reminder?')) return;
    try {
      await notificationService.deleteTrigger(id);
      setTriggers(triggers.filter(t => t.id !== id));
    } catch (error) {
      alert('Failed to delete trigger');
    }
  };

  const handleFixPush = async () => {
    try {
      const { nativeNotificationService } = await import('../../services/nativeNotificationService');
      
      // Check permission first
      if (window.Notification && window.Notification.permission === 'denied') {
        alert('Browser notification permission is currently DENIED. Please click the lock icon in your address bar and set Notifications to "Allow" or "Ask", then try again.');
        return;
      }

      const sub = await nativeNotificationService.subscribeToPush();
      if (sub) {
        setPushStatus('Subscribed');
        alert('Push notifications successfully enabled!');
      } else {
        // If we got here, maybe VAPID is missing or user dismissed the prompt
        alert('Could not enable push. This can happen if:\n1. You dismissed the browser permission prompt.\n2. The server VAPID keys are not configured.\n3. You are in a "Private/Incognito" window which blocks push.');
      }
    } catch (error: any) {
      console.error('Push init error:', error);
      alert(`Failed to initialize push service: ${error.message || 'Unknown error'}`);
    }
  };

  if (loading && notifications.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <RefreshCw className="w-8 h-8 text-blue-600 animate-spin mb-4" />
        <p className="text-gray-500">Loading notification center...</p>
      </div>
    );
  }

  if (!currentPatient) {
    return <NoPatientState icon={Bell} contextKey="notifications" />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Notification Center"
        subtitle={
          <div className="flex items-center mt-1 space-x-2">
            <p className="text-gray-500 dark:text-dark-muted">
              Reminders and delivery logs
            </p>
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400">
              <span className="w-1 h-1 rounded-full bg-green-500 mr-1 animate-pulse"></span>
              Live
            </span>
            <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium ${pushStatus === 'Subscribed' ? 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-400' : 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400'}`}>
              <Smartphone className="w-3 h-3 mr-1" />
              Push: {pushStatus}
            </span>
          </div>
        }
        icon={<Bell className="w-8 h-8" />}
        showBackButton={true}
      />

      <StickyToolbar
        actions={
          <>
            <button 
              onClick={handleFixPush}
              className={`flex items-center px-3 py-2 border rounded-lg text-xs font-bold transition-colors ${pushStatus === 'Subscribed' ? 'bg-indigo-50 text-indigo-700 border-indigo-200 hover:bg-indigo-100' : 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100'}`}
            >
              <ShieldAlert className="w-3.5 h-3.5 mr-1.5" />
              {pushStatus === 'Checking...' ? 'Initialize' : (pushStatus === 'Subscribed' ? 'Sync Push Registration' : 'Enable Push')}
            </button>
            <button 
              onClick={() => {
                setLoading(true);
                loadData();
              }}
              className="p-2 bg-white dark:bg-dark-surface border border-gray-200 dark:border-dark-border rounded-lg text-gray-500 hover:text-blue-600 transition-colors shadow-sm"
            >
              <RefreshCw className={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </>
        }
      />

      <div className="flex space-x-1 p-1 bg-gray-100 dark:bg-dark-border rounded-xl w-fit">
        <button
          onClick={() => setActiveTab('history')}
          className={`px-4 py-2 text-sm font-bold rounded-lg transition-all ${activeTab === 'history' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
        >
          Delivery History
        </button>
        <button
          onClick={() => setActiveTab('triggers')}
          className={`px-4 py-2 text-sm font-bold rounded-lg transition-all ${activeTab === 'triggers' ? 'bg-white dark:bg-dark-surface text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
        >
          Active Triggers
        </button>
      </div>

      {activeTab === 'history' ? (
        <div className="bg-white dark:bg-dark-surface rounded-2xl shadow-sm border border-gray-100 dark:border-dark-border overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead className="bg-gray-50 dark:bg-dark-bg/50 border-b border-gray-100 dark:border-dark-border">
                <tr>
                  <th className="px-6 py-4 text-xs font-bold text-gray-400 uppercase tracking-wider">Type / Message</th>
                  <th className="px-6 py-4 text-xs font-bold text-gray-400 uppercase tracking-wider">Channel</th>
                  <th className="px-6 py-4 text-xs font-bold text-gray-400 uppercase tracking-wider">Status</th>
                  <th className="px-6 py-4 text-xs font-bold text-gray-400 uppercase tracking-wider">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50 dark:divide-dark-border">
                {notifications.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-6 py-12 text-center text-gray-400">
                      No notifications recorded yet.
                    </td>
                  </tr>
                ) : (
                  notifications.map((n) => (
                    <tr key={n.id} className="hover:bg-gray-50/50 dark:hover:bg-dark-border/30 transition-colors">
                      <td className="px-6 py-4">
                        <div className="flex items-center space-x-3">
                          <div className="w-8 h-8 rounded-lg bg-blue-50 dark:bg-blue-900/20 flex items-center justify-center text-blue-600">
                            <Bell className="w-4 h-4" />
                          </div>
                          <div>
                            <p className="text-sm font-bold text-gray-900 dark:text-dark-text">{n.title}</p>
                            <p className="text-xs text-gray-500 dark:text-dark-muted line-clamp-1">{n.body}</p>
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center space-x-1.5 text-xs font-medium text-gray-600 dark:text-dark-muted">
                          {n.channel === 'push' ? <Smartphone className="w-3.5 h-3.5" /> : <Bell className="w-3.5 h-3.5" />}
                          <span className="capitalize">{n.channel.replace('_', ' ')}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center">
                          {n.status === 'delivered' || n.status === 'read' ? (
                            <span className={`px-2 py-1 ${n.status === 'read' ? 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400' : 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400'} text-[10px] font-bold rounded-full flex items-center uppercase`}>
                              <CheckCircle2 className="w-3 h-3 mr-1" />
                              {n.status}
                            </span>
                          ) : n.status === 'failed' ? (
                            <span className="px-2 py-1 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 text-[10px] font-bold rounded-full flex items-center uppercase">
                              <XCircle className="w-3 h-3 mr-1" />
                              Failed
                            </span>
                          ) : (
                            <span className="px-2 py-1 bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400 text-[10px] font-bold rounded-full flex items-center uppercase">
                              <Clock className="w-3 h-3 mr-1" />
                              Pending
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-6 py-4 text-xs text-gray-400 dark:text-dark-muted">
                        {formatDistanceToNow(new Date(n.created_at), { addSuffix: true, locale: dateLocale })}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {triggers.length === 0 ? (
            <div className="col-span-full py-20 text-center bg-white dark:bg-dark-surface rounded-2xl border-2 border-dashed border-gray-100 dark:border-dark-border">
              <Clock className="w-12 h-12 text-gray-200 mx-auto mb-4" />
              <p className="text-gray-400">No active scheduled triggers found.</p>
            </div>
          ) : (
            triggers.map((t) => (
              <div key={t.id} className="bg-white dark:bg-dark-surface rounded-2xl p-5 border border-gray-100 dark:border-dark-border shadow-sm hover:shadow-md transition-all">
                <div className="flex items-start justify-between mb-4">
                  <div className={`p-2 rounded-xl ${t.enabled ? 'bg-indigo-50 text-indigo-600 dark:bg-indigo-900/20 dark:text-indigo-400' : 'bg-gray-50 text-gray-400 dark:bg-dark-bg'}`}>
                    <Calendar className="w-5 h-5" />
                  </div>
                  <div className="flex items-center space-x-1">
                    <button 
                      onClick={() => handleTestTrigger(t.id)}
                      title="Test Trigger Now"
                      className="p-2 text-gray-400 hover:text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20 rounded-lg transition-colors"
                    >
                      <Play className="w-4 h-4" />
                    </button>
                    <button 
                      onClick={() => handleDeleteTrigger(t.id)}
                      className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                <h3 className="font-bold text-gray-900 dark:text-dark-text mb-1">{t.title}</h3>
                <p className="text-xs text-gray-500 dark:text-dark-muted mb-4 line-clamp-2">{t.body}</p>

                <div className="space-y-2 pt-4 border-t border-gray-50 dark:border-dark-border">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-gray-400 uppercase font-bold tracking-wider">Type</span>
                    <span className="text-gray-900 dark:text-dark-text font-semibold capitalize">{t.trigger_type}</span>
                  </div>
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-gray-400 uppercase font-bold tracking-wider">Next Run</span>
                    <span className="text-blue-600 dark:text-blue-400 font-bold">
                      {t.next_trigger ? format(new Date(t.next_trigger), 'MMM d, HH:mm') : 'Disabled'}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-gray-400 uppercase font-bold tracking-wider">Status</span>
                    <div className="flex items-center">
                      {t.enabled ? (
                        <span className="flex h-2 w-2 rounded-full bg-green-500 mr-1.5 animate-pulse"></span>
                      ) : (
                        <span className="flex h-2 w-2 rounded-full bg-gray-300 mr-1.5"></span>
                      )}
                      <span className={t.enabled ? 'text-green-600 dark:text-green-400 font-bold' : 'text-gray-400'}>
                        {t.enabled ? 'Active' : 'Disabled'}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}
      
      <div className="bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 rounded-2xl p-4 flex items-start space-x-3">
        <Info className="w-5 h-5 text-blue-500 flex-shrink-0 mt-0.5" />
        <div className="text-sm text-blue-800 dark:text-blue-300 leading-relaxed">
          <p className="font-bold mb-1">Debug Information</p>
          <p>
            Notifications are sent to your registered browser using Web Push.
            Ensure you allow notifications in your browser settings.
            If testing from localhost, ensure the service worker is active in DevTools.
          </p>
        </div>
      </div>
    </div>
  );
}
