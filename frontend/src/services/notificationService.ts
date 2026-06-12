import axios from '../api/axios';

export interface NotificationTrigger {
  id: string;
  patient_id: string;
  trigger_type: string;
  notification_type: string;
  config: any;
  title: string;
  body?: string;
  enabled: boolean;
  next_trigger?: string;
}

export interface Notification {
  id: string;
  patient_id: string;
  type: string;
  status: 'pending' | 'delivered' | 'read' | 'dismissed' | 'failed';
  channel: 'in_app' | 'push' | 'email' | 'sms';
  title: string;
  body?: string;
  payload?: any;
  delivered_at?: string;
  read_at?: string;
  created_at: string;
}

export const notificationService = {
  /**
   * Get VAPID public key
   */
  async getVapidPublicKey() {
    const response = await axios.get<{ public_key: string }>('/notifications/vapid-public-key');
    return response.data.public_key;
  },

  /**
   * Register Web Push subscription
   */
  async subscribe(subscription: any, deviceId?: string) {
    const response = await axios.post('/notifications/subscribe', {
      subscription,
      device_id: deviceId,
      user_agent: navigator.userAgent
    });
    return response.data;
  },

  /**
   * Fetch notifications for a patient
   */
  async getNotifications(patientId: string, unreadOnly = false) {
    const response = await axios.get<Notification[]>('/notifications', {
      params: { patient_id: patientId, unread_only: unreadOnly }
    });
    return response.data;
  },

  /**
   * Mark a notification as read
   */
  async markAsRead(notificationId: string) {
    const response = await axios.patch(`/notifications/${notificationId}/read`);
    return response.data;
  },

  /**
   * Mark a notification as delivered
   */
  async markAsDelivered(notificationId: string) {
    const response = await axios.patch(`/notifications/${notificationId}/delivered`);
    return response.data;
  },

  /**
   * List triggers for a patient
   */
  async getTriggers(patientId: string) {
    const response = await axios.get<NotificationTrigger[]>('/notifications/triggers', {
      params: { patient_id: patientId }
    });
    return response.data;
  },

  /**
   * Delete a trigger
   */
  async deleteTrigger(triggerId: string) {
    const response = await axios.delete(`/notifications/triggers/${triggerId}`);
    return response.data;
  },

  /**
   * Test a trigger (fire immediately)
   */
  async testTrigger(triggerId: string) {
    const response = await axios.post(`/notifications/triggers/${triggerId}/test`);
    return response.data;
  },

  /**
   * Create a notification trigger (reminder)
   */
  async createTrigger(data: {
    patient_id: string;
    title: string;
    body?: string;
    notification_type?: string;
    trigger_type?: string;
    config: any;
    reference_id?: string;
  }) {
    const response = await axios.post('/notifications/triggers', data);
    return response.data;
  }
};
