import { pickLanguages } from '@neuronection/assistant-ui/languages';

import { SHIPPED_LOCALES } from './locales';

export const UI_LANGUAGES = pickLanguages([...SHIPPED_LOCALES]);
