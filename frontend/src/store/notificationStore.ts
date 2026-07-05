import { create } from 'zustand';
import {
  notificationService,
  type NotificationInboxItem,
  type NotificationEvent,
  type NotificationCategory,
  type NotificationSource,
  type RecipientStatus,
} from '../services/notificationService';

export type { NotificationInboxItem, NotificationEvent } from '../services/notificationService';

interface NotificationState {
  inbox: NotificationInboxItem[];
  unreadCount: number;
  loading: boolean;
  error: string | null;
  /** True once the real-time stream is connected. */
  connected: boolean;

  fetchInbox: (filters?: {
    status?: RecipientStatus;
    category?: NotificationCategory;
    source?: NotificationSource;
    patientId?: string;
  }) => Promise<void>;
  refreshUnreadCount: () => Promise<void>;
  markRead: (recipientId: string) => Promise<void>;
  markDismissed: (recipientId: string) => Promise<void>;
  markAllRead: () => Promise<void>;
  /** Real-time handler: a notification arrived over the WebSocket. */
  onLiveNotification: (event: NotificationEvent) => void;
  setConnected: (connected: boolean) => void;
  clear: () => void;
}

export const useNotificationStore = create<NotificationState>((set, get) => ({
  inbox: [],
  unreadCount: 0,
  loading: false,
  error: null,
  connected: false,

  fetchInbox: async (filters) => {
    try {
      set({ loading: true, error: null });
      const { items } = await notificationService.getInbox({
        status: filters?.status,
        category: filters?.category,
        source: filters?.source,
        patient_id: filters?.patientId,
        limit: 50,
      });
      const unreadCount = items.filter((i) => i.status === 'unread').length;
      set({ inbox: items, unreadCount, loading: false });
    } catch (error: any) {
      set({ error: error.message, loading: false });
    }
  },

  refreshUnreadCount: async () => {
    try {
      const count = await notificationService.getUnreadCount();
      set({ unreadCount: count });
    } catch {
      // Non-fatal: the bell badge will refresh on next inbox fetch.
    }
  },

  markRead: async (recipientId: string) => {
    const prev = get().inbox;
    // Optimistic
    set({
      inbox: prev.map((i) =>
        i.recipient_id === recipientId
          ? { ...i, status: 'read', read_at: new Date().toISOString() }
          : i
      ),
      unreadCount: Math.max(0, get().unreadCount - 1),
    });
    try {
      await notificationService.markRead(recipientId);
    } catch {
      // Revert on failure
      set({ inbox: prev, unreadCount: prev.filter((i) => i.status === 'unread').length });
    }
  },

  markDismissed: async (recipientId: string) => {
    const prev = get().inbox;
    const wasUnread = prev.find((i) => i.recipient_id === recipientId)?.status === 'unread';
    set({
      inbox: prev.map((i) =>
        i.recipient_id === recipientId
          ? { ...i, status: 'dismissed', dismissed_at: new Date().toISOString() }
          : i
      ),
      unreadCount: wasUnread ? Math.max(0, get().unreadCount - 1) : get().unreadCount,
    });
    try {
      await notificationService.markDismissed(recipientId);
    } catch {
      set({ inbox: prev, unreadCount: prev.filter((i) => i.status === 'unread').length });
    }
  },

  markAllRead: async () => {
    const prev = get().inbox;
    set({
      inbox: prev.map((i) =>
        i.status === 'unread' ? { ...i, status: 'read', read_at: new Date().toISOString() } : i
      ),
      unreadCount: 0,
    });
    try {
      await notificationService.markAllRead();
    } catch {
      set({ inbox: prev, unreadCount: prev.filter((i) => i.status === 'unread').length });
    }
  },

  onLiveNotification: (event) => {
    const item: NotificationInboxItem = {
      recipient_id: event.id, // temporary client id until next full fetch
      status: 'unread',
      notification: event,
    };
    set({
      inbox: [item, ...get().inbox].slice(0, 100),
      unreadCount: get().unreadCount + 1,
    });
  },

  setConnected: (connected) => set({ connected }),

  clear: () => set({ inbox: [], unreadCount: 0, error: null }),
}));
