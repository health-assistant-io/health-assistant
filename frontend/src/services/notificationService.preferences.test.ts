import { describe, it, expect, vi, beforeEach } from 'vitest';
import api from '../api/axios';
import { notificationService } from './notificationService';

describe('notificationService preferences', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('getPreferences', () => {
    it('GETs /notifications/preferences with no params by default', async () => {
      api.get = vi.fn().mockResolvedValue({
        data: { preferences: [] },
      }) as any;

      await notificationService.getPreferences();

      expect(api.get).toHaveBeenCalledWith('/notifications/preferences', {
        params: {},
      });
    });

    it('forwards integration_id when scoped to one instance', async () => {
      api.get = vi.fn().mockResolvedValue({
        data: { preferences: [] },
      }) as any;

      await notificationService.getPreferences('abc-123');

      expect(api.get).toHaveBeenCalledWith('/notifications/preferences', {
        params: { integration_id: 'abc-123' },
      });
    });

    it('returns the preferences array (unwrapped)', async () => {
      const prefs = [
        {
          kind_id: 'source:SYSTEM',
          label: 'System notifications',
          group: 'source',
          manage_url: '/notifications/settings',
          mutable: true,
          default_enabled: true,
          enabled: true,
        },
      ];
      api.get = vi.fn().mockResolvedValue({ data: { preferences: prefs } }) as any;

      const result = await notificationService.getPreferences();

      expect(result).toEqual(prefs);
    });
  });

  describe('setPreference', () => {
    it('PUTs the enabled state and URL-encodes the kind_id', async () => {
      api.put = vi.fn().mockResolvedValue({
        data: { status: 'success', kind_id: 'source:SYSTEM', enabled: false },
      }) as any;

      const result = await notificationService.setPreference(
        'source:SYSTEM',
        false
      );

      // Colons must be encoded so the path segment is unambiguous.
      expect(api.put).toHaveBeenCalledWith(
        '/notifications/preferences/source%3ASYSTEM',
        { enabled: false }
      );
      expect(result.enabled).toBe(false);
    });

    it('encodes integration kind ids (three colon-separated segments)', async () => {
      api.put = vi.fn().mockResolvedValue({ data: { status: 'success' } }) as any;

      await notificationService.setPreference(
        'integration:abc-123:sensor_malfunction',
        false
      );

      expect(api.put).toHaveBeenCalledWith(
        '/notifications/preferences/integration%3Aabc-123%3Asensor_malfunction',
        { enabled: false }
      );
    });
  });
});
