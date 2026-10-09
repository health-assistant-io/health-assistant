import type { AIStreamMessage } from '../../services/aiAssistanceService';

export type TranslateFn = (key: string, options?: Record<string, unknown>) => string;

/**
 * Map a streamed AI error to a localized, user-facing message.
 *
 * The backend classifies LLM/provider errors into a stable ``errorType``
 * code (connection / timeout / auth / rate_limit / model_config / generic)
 * and intentionally leaves ``error`` EMPTY so raw SDK text (e.g. OpenAI's
 * "Connection error.") never reaches the user. Soft guard violations use
 * ``errorType === 'guard'`` with the user-facing message in ``error``.
 *
 * Falls back to the raw ``error`` text (legacy/unguarded) or a generic
 * localized message so we never render an empty bubble.
 */
export function resolveStreamErrorMessage(
  t: TranslateFn,
  msg: Pick<AIStreamMessage, 'error' | 'errorType'>,
): string {
  if (msg.errorType === 'guard' && msg.error) {
    return msg.error;
  }
  switch (msg.errorType) {
    case 'connection':
    case 'timeout':
      return t('ai_chat.errors.connection');
    case 'auth':
      return t('ai_chat.errors.auth');
    case 'rate_limit':
      return t('ai_chat.errors.rate_limit');
    case 'model_config':
      return t('ai_chat.errors.model_config');
    default:
      return msg.error || t('ai_chat.errors.generic');
  }
}
