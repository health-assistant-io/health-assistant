import React from 'react';
import type { SetupStep } from '../../../types/setup';
import { RedirectStep } from './RedirectStep';
import { DerivedStep } from './DerivedStep';
import { GuidedExternalStep } from './GuidedExternalStep';

export interface StepRendererProps {
  step: SetupStep;
  /** Localised title (parent translates `title_i18n_key`). */
  title: string;
  /** Localised description, optional. */
  description?: string;
  /** Navigate to a route — passed through to `RedirectStep`. */
  onNavigate?: (route: string) => void;
  /**
   * Custom renderer for `inline_form` steps. The parent resolves the right
   * section component (demographics / contacts / extensions / …) via the
   * INLINE_SECTIONS registry. Returns `null` to fall back to the placeholder.
   */
  renderInlineForm?: (step: SetupStep, title: string) => React.ReactNode;
}

/**
 * Kind-driven step dispatcher (design D7).
 *
 * Reads `step.kind` and renders the matching step component:
 * - `redirect` / `external_config` → `RedirectStep` (deep-link CTA; always
 *   available — "Open" when incomplete, "Manage" when complete)
 * - `derived`                       → `DerivedStep` (read-only status)
 * - `inline_form`                   → `renderInlineForm` prop, else a placeholder
 *
 * Unknown kinds fall back to `DerivedStep` so a new backend kind never
 * crashes the wizard — it just shows as a status line until a renderer is
 * added.
 */
export const StepRenderer: React.FC<StepRendererProps> = ({
  step,
  title,
  description,
  onNavigate,
  renderInlineForm,
}) => {
  switch (step.kind) {
    case 'redirect':
    case 'external_config':
      // If the step carries sub_steps, render a guided multi-step redirect
      // (e.g. AI config: provider → model → tasks) instead of a simple link.
      if (step.payload_hint?.sub_steps?.length) {
        return (
          <GuidedExternalStep
            step={step}
            title={title}
            description={description}
            onNavigate={onNavigate}
          />
        );
      }
      return (
        <RedirectStep
          step={step}
          title={title}
          description={description}
          onNavigate={onNavigate}
        />
      );
    case 'inline_form':
      if (renderInlineForm) {
        const node = renderInlineForm(step, title);
        if (node !== null && node !== undefined) return <>{node}</>;
      }
      // Fallback when no section is registered for this step yet.
      return <DerivedStep step={step} title={title} description={description} />;
    case 'derived':
    default:
      return <DerivedStep step={step} title={title} description={description} />;
  }
};

export default StepRenderer;
