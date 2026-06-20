import axios from '../api/axios';

export type SettingType = 'integer' | 'float' | 'boolean' | 'string' | 'enum';
export type SettingStorage = 'tiered' | 'device';
export type SettingLevel = 'system' | 'tenant' | 'user';

export interface SettingEnumOption {
  value: string;
  label_key: string;
}

export interface SettingDefinition {
  key: string;
  category: string;
  type: SettingType;
  default: any;
  storage: SettingStorage;
  allowed_levels: SettingLevel[];
  label_key: string;
  description_key: string;
  min?: number | null;
  max?: number | null;
  options?: SettingEnumOption[] | null;
  order: number;
}

export interface SettingCategory {
  key: string;
  label_key: string;
  description_key?: string | null;
  order: number;
}

export interface DefinitionsResponse {
  definitions: SettingDefinition[];
  categories: SettingCategory[];
}

export interface EffectiveSettingsResponse {
  settings: Record<string, any>;
  sources: Record<string, string>;
}

export interface LevelOverridesResponse {
  level: SettingLevel;
  settings: Record<string, any>;
}

export const settingsService = {
  async getDefinitions(): Promise<DefinitionsResponse> {
    const r = await axios.get<DefinitionsResponse>('/settings/definitions');
    return r.data;
  },

  async getEffective(): Promise<EffectiveSettingsResponse> {
    const r = await axios.get<EffectiveSettingsResponse>('/settings/effective');
    return r.data;
  },

  async getOverrides(level: SettingLevel): Promise<LevelOverridesResponse> {
    const r = await axios.get<LevelOverridesResponse>(`/settings/${level}`);
    return r.data;
  },

  async updateOverride(level: SettingLevel, key: string, value: any): Promise<void> {
    await axios.put(`/settings/${level}`, { key, value });
  },

  async resetOverride(level: SettingLevel, key: string): Promise<void> {
    await axios.put(`/settings/${level}`, { key, value: null });
  },
};
