import { create } from 'zustand';
import { notificationService, Notification } from '../services/notificationService';

interface NotificationState {
  notifications: Notification[];
  unreadCount: number;
  loading: boolean;
  error: string | null;
  pollInterval: any;
  
  fetchNotifications: (patientId: string) => Promise<void>;
  startPolling: (patientId: string) => void;
  stopPolling: () => void;
  markAsRead: (notificationId: string) => Promise<void>;
  addNotification: (notification: Notification) => void;
  clearNotifications: () => void;
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  notifications: [],
  unreadCount: 0,
  loading: false,
  error: null,
  pollInterval: null as any,

  fetchNotifications: async (patientId: string) => {
    try {
      const notifications = await notificationService.getNotifications(patientId);
      const unreadCount = notifications.filter(n => n.status === 'pending' || n.status === 'delivered').length;
      set({ notifications, unreadCount, loading: false });
    } catch (error: any) {
      set({ error: error.message, loading: false });
    }
  },

  startPolling: (patientId: string) => {
    const { stopPolling, fetchNotifications } = get();
    stopPolling();
    
    // Initial fetch
    fetchNotifications(patientId);
    
    // Set interval for every 30 seconds
    const interval = setInterval(() => {
      fetchNotifications(patientId);
    }, 30000);
    
    set({ pollInterval: interval });
  },

  stopPolling: () => {
    const { pollInterval } = get();
    if (pollInterval) {
      clearInterval(pollInterval);
      set({ pollInterval: null });
    }
  },

  markAsRead: async (notificationId: string) => {
    try {
      await notificationService.markAsRead(notificationId);
      set(state => {
        const updated = state.notifications.map(n => 
          n.id === notificationId ? { ...n, status: 'read' as const, read_at: new Date().toISOString() } : n
        );
        const unreadCount = updated.filter(n => n.status === 'pending' || n.status === 'delivered').length;
        return { notifications: updated, unreadCount };
      });
    } catch (error: any) {
      set({ error: error.message });
    }
  },

  addNotification: (notification: Notification) => {
    set(state => {
      const notifications = [notification, ...state.notifications];
      const unreadCount = notifications.filter(n => n.status === 'pending' || n.status === 'delivered').length;
      return { notifications, unreadCount };
    });
  },

  clearNotifications: () => {
    set({ notifications: [], unreadCount: 0 });
  }
}));
