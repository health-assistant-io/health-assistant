import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Constraints for chat voice recording. Speech needs only ~16 kHz mono, so we
 * request Opus at a low bitrate to keep uploads tiny (~24 kbps → ~90 KB / 30s).
 * The backend re-validates size + MIME at the trust boundary.
 */
export const VOICE_CONSTRAINTS = {
  maxDurationMs: 60_000,
  bitsPerSecond: 24_000,
  channelCount: 1,
  sampleRate: 16_000,
} as const;

/** Preferred MIME the browser can record. Falls back across candidates. */
const PREFERRED_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
];

function pickSupportedMime(): string {
  if (typeof MediaRecorder === 'undefined') return '';
  for (const type of PREFERRED_MIME_TYPES) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return '';
}

export type RecorderStatus = 'idle' | 'recording' | 'stopping';

export interface UseVoiceRecorderResult {
  /** Whether recording is supported in this browser. */
  isSupported: boolean;
  status: RecorderStatus;
  /** Elapsed recording time in milliseconds (live, for the UI timer). */
  elapsedMs: number;
  /** The MIME that will be used (for the filename extension). */
  mimeType: string;
  start: () => Promise<void>;
  stop: () => Promise<Blob | null>;
  /** Cancel an in-flight recording and discard the audio. */
  cancel: () => void;
}

/**
 * Capture compressed microphone audio for speech-to-text.
 *
 * Wraps ``MediaRecorder`` requesting Opus/16 kHz mono/24 kbps so the resulting
 * Blob is small before it ever hits the network. The hook is UI-agnostic: the
 * caller decides the interaction model (push-to-talk vs toggle) by calling
 * ``start`` / ``stop``. An automatic hard stop kicks in at
 * ``VOICE_CONSTRAINTS.maxDurationMs`` to cap upload size.
 *
 * ``elapsedMs`` updates via ``setInterval`` for the live timer/waveform.
 */
export function useVoiceRecorder(): UseVoiceRecorderResult {
  const [isSupported] = useState(
    () =>
      typeof navigator !== 'undefined' &&
      !!navigator.mediaDevices?.getUserMedia &&
      typeof MediaRecorder !== 'undefined',
  );
  const [status, setStatus] = useState<RecorderStatus>('idle');
  const [elapsedMs, setElapsedMs] = useState(0);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const resolveRef = useRef<((b: Blob | null) => void) | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startRef = useRef<number>(0);
  const hardStopRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mimeType = useState(() => pickSupportedMime())[0];

  const teardownStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (hardStopRef.current) {
      clearTimeout(hardStopRef.current);
      hardStopRef.current = null;
    }
  }, []);

  const stop = useCallback((): Promise<Blob | null> => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === 'inactive') {
      setStatus('idle');
      clearTimer();
      teardownStream();
      return Promise.resolve(null);
    }
    setStatus('stopping');
    return new Promise<Blob | null>((resolve) => {
      resolveRef.current = resolve;
      try {
        recorder.stop();
      } catch {
        resolve(null);
      }
    });
  }, [clearTimer, teardownStream]);

  const cancel = useCallback(() => {
    resolveRef.current = null;
    chunksRef.current = [];
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== 'inactive') {
      try {
        recorder.stop();
      } catch {
        /* ignore */
      }
    }
    clearTimer();
    teardownStream();
    setStatus('idle');
    setElapsedMs(0);
  }, [clearTimer, teardownStream]);

  const start = useCallback(async () => {
    if (!isSupported) throw new Error('Voice recording is not supported in this browser.');
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') return;

    chunksRef.current = [];
    setElapsedMs(0);

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: VOICE_CONSTRAINTS.channelCount,
        sampleRate: VOICE_CONSTRAINTS.sampleRate,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    streamRef.current = stream;

    const options: MediaRecorderOptions = {
      audioBitsPerSecond: VOICE_CONSTRAINTS.bitsPerSecond,
    };
    if (mimeType) options.mimeType = mimeType;
    const recorder = new MediaRecorder(stream, options);
    mediaRecorderRef.current = recorder;

    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      clearTimer();
      teardownStream();
      const type = recorder.mimeType || mimeType || 'audio/webm';
      const blob =
        chunksRef.current.length > 0 ? new Blob(chunksRef.current, { type }) : null;
      chunksRef.current = [];
      const resolve = resolveRef.current;
      resolveRef.current = null;
      setStatus('idle');
      setElapsedMs(0);
      if (resolve) resolve(blob);
    };

    startRef.current = Date.now();
    timerRef.current = setInterval(() => {
      setElapsedMs(Date.now() - startRef.current);
    }, 100);

    // Hard cap: auto-stop at max duration to bound upload size.
    hardStopRef.current = setTimeout(() => {
      void stop();
    }, VOICE_CONSTRAINTS.maxDurationMs);

    recorder.start();
    setStatus('recording');
  }, [isSupported, mimeType, clearTimer, teardownStream, stop]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      cancel();
    };
  }, [cancel]);

  return { isSupported, status, elapsedMs, mimeType, start, stop, cancel };
}
