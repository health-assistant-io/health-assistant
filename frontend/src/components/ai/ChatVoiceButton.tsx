import React, { useRef } from 'react';
import { Mic, MicOff, Square, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { UseVoiceRecorderResult } from '../../hooks/useVoiceRecorder';

interface Props {
  /** Called when the gesture completes a recording that should be transcribed
   *  (push-to-talk release, or the second tap of toggle mode). */
  onFinish: () => void;
  /** Called when the user aborts the recording (slide-off in push-to-talk).
   *  The parent discards the audio without transcribing. */
  onCancel: () => void;
  onError?: (message: string) => void;
  disabled?: boolean;
  /** The recorder instance, owned by the parent so it can render a recording
   *  banner above the input box (outside its ``overflow-hidden`` container)
   *  AND drive finish/cancel for both the gesture and the banner buttons. */
  recorder: UseVoiceRecorderResult;
}

/** Threshold below which a press is a "tap" (toggle mode) vs press-hold
 *  (push-to-talk). 250 ms is a comfortable boundary for a deliberate hold. */
const HOLD_THRESHOLD_MS = 250;

/**
 * Unified mic button for voice input.
 *
 * One control handles BOTH interaction models by gesture:
 *   * **Tap** (pointer down→up < HOLD_THRESHOLD_MS) → **toggle** mode:
 *     first tap starts recording, the second tap finishes (stop + transcribe).
 *   * **Press & hold** (down ≥ HOLD_THRESHOLD_MS) → **push-to-talk**:
 *     recording runs while held; releasing ON the button finishes, while
 *     sliding the pointer OFF the button first aborts (discards the audio) —
 *     the messenger-style "slide to cancel".
 *
 * The button only owns gesture detection + starting the recorder. Finishing
 * (stop + transcribe) and cancelling (discard) are delegated to the parent via
 * ``onFinish`` / ``onCancel`` so the parent's recording banner buttons can
 * trigger the exact same outcomes.
 */
export const ChatVoiceButton: React.FC<Props> = ({
  onFinish,
  onCancel,
  onError,
  disabled,
  recorder,
}) => {
  const { t } = useTranslation();
  const pointerDownAt = useRef<number | null>(null);
  /** True once the press has exceeded the hold threshold (push-to-talk mode).
   * Stays set until pointer up so we know the release is the stop. */
  const heldRef = useRef(false);
  /** Toggle mode: whether a tap started an active recording awaiting another
   * tap to finish. */
  const toggleActiveRef = useRef(false);

  const beginRecording = async () => {
    try {
      await recorder.start();
    } catch (err) {
      onError?.(
        err instanceof Error
          ? err.message
          : t('ai_chat.voice.permission_denied', {
              defaultValue: 'Microphone access denied.',
            }),
      );
    }
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (disabled || !recorder.isSupported) return;
    e.preventDefault();
    pointerDownAt.current = Date.now();
    heldRef.current = false;
    // If a toggle-mode recording is already running, this press will finish it.
    if (toggleActiveRef.current) return;
    // Schedule the "hold" → start push-to-talk recording after the threshold.
    window.setTimeout(() => {
      if (pointerDownAt.current !== null && !toggleActiveRef.current) {
        heldRef.current = true;
        void beginRecording();
      }
    }, HOLD_THRESHOLD_MS);
  };

  const onPointerUp = (e: React.PointerEvent) => {
    if (disabled) return;
    e.preventDefault();
    if (pointerDownAt.current === null) return;
    pointerDownAt.current = null;

    if (heldRef.current) {
      // Push-to-talk release on the button → finish (transcribe).
      heldRef.current = false;
      onFinish();
      return;
    }

    // It was a tap (short press).
    if (toggleActiveRef.current) {
      // Second tap → finish the toggle recording (transcribe).
      toggleActiveRef.current = false;
      onFinish();
    } else {
      // First tap → start a toggle recording.
      toggleActiveRef.current = true;
      void beginRecording();
    }
  };

  const onPointerLeave = (e: React.PointerEvent) => {
    // Push-to-talk: sliding the pointer off the button aborts (slide to
    // cancel). Toggle-mode recordings are cancelled via the banner button.
    if (heldRef.current && pointerDownAt.current !== null) {
      pointerDownAt.current = null;
      heldRef.current = false;
      onCancel();
      e.preventDefault();
    }
  };

  const recording = recorder.status === 'recording';
  const stopping = recorder.status === 'stopping';

  return (
    <button
      type="button"
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerLeave}
      disabled={disabled || !recorder.isSupported}
      title={
        !recorder.isSupported
          ? t('ai_chat.voice.unsupported', { defaultValue: 'Voice input not supported' })
          : recording
            ? t('ai_chat.voice.cancel_hint', {
                defaultValue: 'Release to send • slide off to cancel',
              })
            : t('ai_chat.voice.record', { defaultValue: 'Hold to talk • tap to toggle' })
      }
      className={`p-2 rounded-xl transition-all shrink-0 disabled:opacity-40 disabled:cursor-not-allowed ${
        recording
          ? 'bg-red-500 text-white shadow-lg shadow-red-500/30 animate-pulse'
          : 'text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-dark-bg'
      }`}
    >
      {stopping ? (
        <Loader2 className="w-4 h-4 md:w-[18px] md:h-[18px] animate-spin" />
      ) : recording ? (
        <Square className="w-4 h-4 md:w-[18px] md:h-[18px]" fill="currentColor" />
      ) : !recorder.isSupported ? (
        <MicOff className="w-4 h-4 md:w-[18px] md:h-[18px]" />
      ) : (
        <Mic className="w-4 h-4 md:w-[18px] md:h-[18px]" />
      )}
    </button>
  );
};
