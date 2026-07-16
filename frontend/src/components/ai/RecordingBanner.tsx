import React from 'react';
import { Square, Loader2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { UseVoiceRecorderResult } from '../../hooks/useVoiceRecorder';

interface Props {
  recorder: UseVoiceRecorderResult;
  /** True while the audio is being sent to the STT provider. */
  transcribing?: boolean;
  /** Finish: stop the recording and transcribe it (Stop button). */
  onStop: () => void;
  /** Abort: stop the recording and discard the audio (Cancel button). */
  onCancel: () => void;
}

/**
 * Recording indicator shown ABOVE the chat input box (outside its
 * ``overflow-hidden`` rounded container so it is never clipped).
 *
 * Renders a compact pill with a pulsing dot and a live ``M:SS`` timer plus two
 * actions: **Stop** (finish → transcribe) and **Cancel** (abort → discard).
 * While transcribing it swaps to a spinner + "Transcribing…".
 */
export const RecordingBanner: React.FC<Props> = ({
  recorder,
  transcribing,
  onStop,
  onCancel,
}) => {
  const { t } = useTranslation();
  const recording = recorder.status === 'recording';
  if (!recording && !transcribing) return null;

  const totalSeconds = Math.floor(recorder.elapsedMs / 1000);
  const mm = Math.floor(totalSeconds / 60);
  const ss = totalSeconds % 60;
  const timer = `${mm}:${ss.toString().padStart(2, '0')}`;

  const stop = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onStop();
  };
  const cancel = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onCancel();
  };

  return (
    <div className="relative z-10 flex items-center justify-center px-1 pb-2">
      <div
        className={`inline-flex items-center gap-2.5 pl-3 pr-1.5 py-1.5 rounded-full border shadow-sm ${
          transcribing
            ? 'bg-indigo-50 dark:bg-indigo-900/20 border-indigo-200 dark:border-indigo-800 text-indigo-600 dark:text-indigo-300'
            : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-600 dark:text-red-300'
        }`}
      >
        {transcribing ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75 animate-ping" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-500" />
          </span>
        )}
        <span className="text-xs font-bold uppercase tracking-wider">
          {transcribing
            ? t('ai_chat.voice.transcribing', { defaultValue: 'Transcribing…' })
            : t('ai_chat.voice.recording', { defaultValue: 'Recording' })}
        </span>
        {!transcribing && (
          <span className="text-xs font-black tabular-nums opacity-80">{timer}</span>
        )}
        {!transcribing && recording && (
          <>
            <button
              type="button"
              onClick={cancel}
              className="inline-flex items-center justify-center w-6 h-6 rounded-full text-red-600 dark:text-red-300 hover:bg-red-100 dark:hover:bg-red-900/40 transition-colors active:scale-95"
              title={t('ai_chat.voice.cancel', { defaultValue: 'Cancel recording' })}
            >
              <X className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              onClick={stop}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-red-600 text-white text-[10px] font-black uppercase tracking-wider hover:bg-red-700 transition-colors active:scale-95"
              title={t('ai_chat.voice.stop', { defaultValue: 'Stop & transcribe' })}
            >
              <Square className="w-2.5 h-2.5" fill="currentColor" />
              {t('ai_chat.voice.stop_label', { defaultValue: 'Stop' })}
            </button>
          </>
        )}
      </div>
    </div>
  );
};

