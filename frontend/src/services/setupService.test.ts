import { describe, it, expect, vi, beforeEach } from 'vitest';
import api from '../api/axios';
import { getSetupChecklist, getExtensionCatalog } from './setupService';
import type { SetupChecklist, ExtensionCatalog } from '../types/setup';

describe('setupService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('getSetupChecklist', () => {
    it('GETs /setup/checklist with no params by default', async () => {
      const mock: SetupChecklist = {
        role: 'USER',
        entity: null,
        entity_id: null,
        steps: [
          {
            id: 'user.preferences_language',
            title_i18n_key: 'setup.steps.user.preferences_language',
            kind: 'external_config',
            completed: false,
            optional: false,
          },
        ],
        completion: 0,
      };
      api.get = vi.fn().mockResolvedValue({ data: mock }) as any;

      const result = await getSetupChecklist();

      expect(api.get).toHaveBeenCalledWith('/setup/checklist', { params: undefined });
      expect(result.role).toBe('USER');
      expect(result.steps).toHaveLength(1);
    });

    it('forwards entity + entity_id as query params', async () => {
      api.get = vi.fn().mockResolvedValue({ data: { steps: [] } }) as any;

      await getSetupChecklist({ entity: 'patient', entity_id: 'p-123' });

      expect(api.get).toHaveBeenCalledWith('/setup/checklist', {
        params: { entity: 'patient', entity_id: 'p-123' },
      });
    });

    it('returns the parsed checklist body', async () => {
      const mock: SetupChecklist = {
        role: 'ADMIN',
        entity: 'patient',
        entity_id: 'p-1',
        steps: [
          {
            id: 'patient.birth_date',
            entity: 'patient',
            title_i18n_key: 'setup.steps.patient.birth_date',
            kind: 'inline_form',
            completed: true,
            optional: false,
          },
          {
            id: 'patient.allergies',
            entity: 'patient',
            title_i18n_key: 'setup.steps.patient.allergies',
            kind: 'redirect',
            completed: false,
            optional: true,
            payload_hint: { route: '/patients/p-1?section=allergies' },
          },
        ],
        completion: 1,
      };
      api.get = vi.fn().mockResolvedValue({ data: mock }) as any;

      const result = await getSetupChecklist({ entity: 'patient', entity_id: 'p-1' });

      expect(result.completion).toBe(1);
      expect(result.steps[1].payload_hint?.route).toBe('/patients/p-1?section=allergies');
    });
  });

  describe('getExtensionCatalog', () => {
    it('GETs /setup/extension-catalog with no params by default', async () => {
      const mock: ExtensionCatalog = {
        entity: 'patient',
        extensions: [
          {
            key: 'race',
            title_i18n_key: 'patient.setup.extension.race',
            value_type: 'omb_category',
            cardinality: '0..1',
            options: [{ code: '2106-3', display: 'White' }],
          },
        ],
      };
      api.get = vi.fn().mockResolvedValue({ data: mock }) as any;

      const result = await getExtensionCatalog();

      expect(api.get).toHaveBeenCalledWith('/setup/extension-catalog', {
        params: undefined,
      });
      expect(result.extensions).toHaveLength(1);
      expect(result.extensions[0].value_type).toBe('omb_category');
    });

    it('forwards entity param when provided', async () => {
      api.get = vi.fn().mockResolvedValue({ data: { extensions: [] } }) as any;

      await getExtensionCatalog('patient');

      expect(api.get).toHaveBeenCalledWith('/setup/extension-catalog', {
        params: { entity: 'patient' },
      });
    });
  });
});
