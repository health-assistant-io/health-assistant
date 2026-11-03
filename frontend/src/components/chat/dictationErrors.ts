import type { DictationError } from '../ui/dictation';

/**
 * Map health's transcription-endpoint failures onto the library dictation
 * error kinds. The endpoint answers 415 for foreign audio formats, 400 with
 * a "no … model" detail when the transcription task is unassigned, and
 * 429/5xx from the gateway — `transcribeAudio` flattens all of them into a
 * thrown Error whose message carries the HTTP status or the detail text.
 */
export function classifyDictationError(error: unknown): DictationError {
  const message = error instanceof Error ? error.message : String(error);
  if (/unsupported audio format/i.test(message)) {
    return { kind: 'unsupported' };
  }
  if (/HTTP (409|503)/.test(message) || /no .*model/i.test(message)) {
    return { kind: 'unassigned' };
  }
  if (/HTTP 403|permission/i.test(message)) {
    return { kind: 'denied' };
  }
  return { kind: 'failed', detail: message };
}
