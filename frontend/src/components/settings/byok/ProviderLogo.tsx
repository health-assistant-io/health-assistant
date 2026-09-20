import {
  siDeepseek,
  siMistralai,
  siOllama,
  siOpenrouter,
} from 'simple-icons';

const ICON_PATHS: Record<string, string> = {
  openrouter: siOpenrouter.path,
  mistral: siMistralai.path,
  deepseek: siDeepseek.path,
  ollama: siOllama.path,
};

export function ProviderLogo({
  presetKey,
  label,
}: {
  presetKey: string | null;
  label: string;
}) {
  const path = presetKey ? ICON_PATHS[presetKey] : undefined;
  if (path) {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="h-5 w-5 shrink-0" fill="currentColor">
        <path d={path} />
      </svg>
    );
  }
  return (
    <span
      aria-hidden="true"
      className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-gray-200 text-[10px] font-semibold text-gray-400 dark:border-dark-border dark:text-dark-muted"
    >
      {label.charAt(0)}
    </span>
  );
}
