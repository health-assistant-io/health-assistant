import axios from '../api/axios';

// ---------------------------------------------------------------------------
// Types — mirror backend app/schemas/notification.py
// ---------------------------------------------------------------------------

export type NotificationStatus = 'pending' | 'sent' | 'delivered' | 'failed';
export type RecipientStatus = 'unread' | 'read' | 'dismissed';
export type NotificationChannel = 'IN_APP' | 'PUSH' | 'EMAIL' | 'SMS';
export type NotificationSource =
  | 'SYSTEM' | 'INTEGRATION' | 'AGENT' | 'RULE' | 'CLINICAL' | 'SCHEDULED';
export type NotificationCategory =
  | 'reminder' | 'alert' | 'hitl' | 'agent' | 'system' | 'integration' | 'clinical_event';
export type NotificationSeverity = 'info' | 'warning' | 'critical';

export interface NotificationAction {
  id: string;
  label: string;
  type: 'link' | 'post';
  url?: string;
  endpoint?: string;
  method?: string;
  style?: string;
}

export interface NotificationEvent {
  id: string;
  patient_id?: string | null;
  trigger_id?: string | null;
  communication_id?: string | null;
  source: NotificationSource;
  type: string;
  category: NotificationCategory;
  severity: NotificationSeverity;
  title: string;
  body?: string | null;
  payload?: Record<string, unknown> & {
    actions?: NotificationAction[];
    display_blocks?: any[];
  };
  source_ref?: Record<string, unknown>;
  sender_user_id?: string | null;
  tenant_id?: string | null;
  created_at?: string | null;
}

export interface NotificationInboxItem {
  recipient_id: string;
  status: RecipientStatus;
  read_at?: string | null;
  dismissed_at?: string | null;
  notification: NotificationEvent;
}

export interface InboxResponse {
  items: NotificationInboxItem[];
  total: number;
}

export interface NotificationTrigger {
  id: string;
  patient_id?: string | null;
  trigger_type: string;
  notification_type: string;
  config: any;
  title: string;
  body?: string;
  enabled: boolean;
  next_trigger?: string | null;
  reference_id?: string | null;
  created_at?: string | null;
}

export interface TargetSpec {
  kind: 'USER' | 'PATIENT' | 'DOCTOR' | 'TENANT' | 'SYSTEM';
  id?: string;
}

export interface NotificationRule {
  id: string;
  tenant_id?: string | null;
  rule_type: string;
  biomarker_id?: string | null;
  operator?: string | null;
  value?: number | null;
  patient_id?: string | null;
  severity: string;
  enabled: boolean;
  cooldown_minutes: number;
  last_fired_at?: string | null;
  targets: TargetSpec[];
  title_template?: string | null;
  body_template?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface InboxFilters {
  status?: RecipientStatus;
  category?: NotificationCategory;
  source?: NotificationSource;
  patient_id?: string;
  limit?: number;
  offset?: number;
}

// ---------------------------------------------------------------------------
// Admin delivery detail
// ---------------------------------------------------------------------------

export type DeliveryChannel = 'IN_APP' | 'PUSH' | 'EMAIL' | 'SMS';
export type DeliveryStatus = 'PENDING' | 'SENT' | 'DELIVERED' | 'FAILED';

export interface DeliveryAttempt {
  channel: DeliveryChannel;
  status: DeliveryStatus;
  attempted_at?: string | null;
  delivered_at?: string | null;
  error?: string | null;
}

export interface DeliveryRecipient {
  user_id: string;
  user_email: string | null;
  inbox_status: RecipientStatus;
  read_at?: string | null;
  dismissed_at?: string | null;
  recipient_kind: string;
  deliveries: DeliveryAttempt[];
}

export interface DeliveryDetail {
  notification: NotificationEvent;
  sender: { id: string; email: string } | null;
  recipients: DeliveryRecipient[];
  recipient_count: number;
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

export const notificationService = {
  /** Get VAPID public key. */
  async getVapidPublicKey() {
    const response = await axios.get<{ public_key: string }>('/notifications/vapid-public-key');
    return response.data.public_key;
  },

  /** Register a Web Push subscription. */
  async subscribe(subscription: unknown, deviceId?: string) {
    const response = await axios.post('/notifications/subscribe', {
      subscription,
      device_id: deviceId,
      user_agent: navigator.userAgent,
    });
    return response.data;
  },

  /** Fetch the current user's personal inbox. */
  async getInbox(filters: InboxFilters = {}) {
    const response = await axios.get<InboxResponse>('/notifications/inbox', {
      params: {
        status: filters.status,
        category: filters.category,
        source: filters.source,
        patient_id: filters.patient_id,
        limit: filters.limit ?? 50,
        offset: filters.offset ?? 0,
      },
    });
    return response.data;
  },

  /** Badge count for the bell. */
  async getUnreadCount() {
    const response = await axios.get<{ count: number }>('/notifications/unread-count');
    return response.data.count;
  },

  /** Admin / tenant-wide feed. */
  async getAdminFeed(params: InboxFilters & { type?: string; tenant_id?: string } = {}) {
    const response = await axios.get<{ items: NotificationEvent[]; total: number }>('/notifications/admin', {
      params: {
        tenant_id: params.tenant_id,
        type: params.type,
        source: params.source,
        category: params.category,
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      },
    });
    return response.data;
  },

  /** Aggregated delivery stats (admin only). */
  async getAdminStats(tenantId?: string) {
    const response = await axios.get<{
      by_source: Record<string, number>;
      by_category: Record<string, number>;
      delivery: Record<string, Record<string, number>>;
      recipients: number;
      unique_recipients: number;
      total: number;
    }>('/notifications/admin/stats', { params: { tenant_id: tenantId } });
    return response.data;
  },

  /** Per-recipient delivery detail for a single notification (admin only). */
  async getDeliveryDetail(notificationId: string) {
    const response = await axios.get<DeliveryDetail>(
      `/notifications/admin/${notificationId}/delivery`
    );
    return response.data;
  },

  /** Broadcast a system notification (admin only). */
  async broadcast(data: {
    title: string;
    body?: string;
    severity?: string;
    scope?: 'tenant' | 'system';
    tenant_id?: string;
  }) {
    const response = await axios.post<{ status: string; notification_id: string }>(
      '/admin/notifications/broadcast',
      null,
      { params: data }
    );
    return response.data;
  },

  /** Mark a recipient inbox row as read. */
  async markRead(recipientId: string) {
    const response = await axios.patch(`/notifications/${recipientId}/read`);
    return response.data;
  },

  /** Dismiss a recipient inbox row. */
  async markDismissed(recipientId: string) {
    const response = await axios.patch(`/notifications/${recipientId}/dismiss`);
    return response.data;
  },

  /** Mark every unread inbox row as read. */
  async markAllRead() {
    const response = await axios.post<{ marked_read: number }>('/notifications/read-all');
    return response.data;
  },

  // --- Scheduled triggers (reminders) -------------------------------------

  async getTriggers(patientId?: string) {
    const response = await axios.get<NotificationTrigger[]>('/notifications/triggers', {
      params: patientId ? { patient_id: patientId } : {},
    });
    return response.data;
  },

  async createTrigger(data: {
    patient_id?: string;
    title: string;
    body?: string;
    notification_type?: string;
    trigger_type?: string;
    config: any;
    reference_id?: string;
    enabled?: boolean;
  }) {
    const response = await axios.post('/notifications/triggers', data);
    return response.data;
  },

  async deleteTrigger(triggerId: string) {
    const response = await axios.delete(`/notifications/triggers/${triggerId}`);
    return response.data;
  },

  async testTrigger(triggerId: string) {
    const response = await axios.post(`/notifications/triggers/${triggerId}/test`);
    return response.data;
  },

  // --- Notification rules --------------------------------------------------

  async listRules(params: { patient_id?: string; biomarker_id?: string; enabled?: boolean } = {}) {
    const response = await axios.get<{ items: NotificationRule[]; total: number }>(
      '/notification-rules',
      { params }
    );
    return response.data.items;
  },

  async createRule(data: Partial<NotificationRule> & { rule_type: string }) {
    const response = await axios.post<NotificationRule>('/notification-rules', {
      rule_type: data.rule_type,
      biomarker_id: data.biomarker_id,
      operator: data.operator,
      value: data.value,
      patient_id: data.patient_id,
      severity: data.severity ?? 'warning',
      enabled: data.enabled ?? true,
      cooldown_minutes: data.cooldown_minutes ?? 60,
      targets: data.targets ?? [],
      title_template: data.title_template,
      body_template: data.body_template,
    });
    return response.data;
  },

  async updateRule(ruleId: string, data: Partial<NotificationRule>) {
    const response = await axios.put<NotificationRule>(`/notification-rules/${ruleId}`, data);
    return response.data;
  },

  async deleteRule(ruleId: string) {
    const response = await axios.delete(`/notification-rules/${ruleId}`);
    return response.data;
  },

  async testRule(ruleId: string) {
    const response = await axios.post(`/notification-rules/${ruleId}/test`);
    return response.data;
  },
};
