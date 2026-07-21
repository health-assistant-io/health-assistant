/**
 * AskUserHandler — inline HITL handler for ``ask_user`` tasks.
 *
 * Renders the LLM's batched questions directly in the chat card body (no
 * modal). The user fills in the answers and clicks Submit — the answers are
 * sent to ``/resolve`` (status: confirmed) and the agent receives them on
 * the next turn via the standard HITL resume feedback.
 *
 * Read-only: ``ask_user`` performs no REST write. The answers flow
 * LLM→JSONB→LLM only (preserving the "AI never writes" model).
 *
 * Five question kinds are routed through ``<QuestionRouter>``:
 *   - freetext        — textarea/input.
 *   - single_choice   — radio list.
 *   - multi_choice    — checkbox list (with min/max).
 *   - catalog_ref     — {@link EntityPicker} bound to ``searchCatalogs``.
 *   - instance_ref    — {@link EntityPicker} bound to ``searchInstances``.
 *
 * Unknown question kinds fall back to a read-only "unsupported" notice so a
 * forward-compat backend addition never breaks the chat surface.
 */
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Loader2, Send, SkipForward } from 'lucide-react';
import type {
  AskUserAnswers,
  AskUserPayload,
  AskUserQuestion,
  QuestionCandidate,
  TaskInfo,
} from '../../../../types/ai';
import { HitlHandlerProps } from '../registry';
import { resolveHitlTask } from '../../../../services/aiAssistanceService';
import { CatalogItemPicker } from '../../../catalog/CatalogItemPicker';
import { InstancePicker } from '../../../instances/InstancePicker';
import type { CatalogSelection, CatalogType } from '../../../../types/catalog';
import type { InstanceSelection, InstanceType } from '../../../instances/types';

// ---------------------------------------------------------------------------
// Summary renderer (compact chip for the proposed-state card header area)
// ---------------------------------------------------------------------------

export function renderAskUserSummary(task: TaskInfo): React.ReactNode {
  const payload = (task.proposed_payload || {}) as Partial<AskUserPayload>;
  const n = payload.questions?.length ?? 0;
  if (!n) return null;
  return (
    <div className="text-[11px] text-gray-600 dark:text-dark-muted">
      <span className="font-bold text-indigo-600 dark:text-indigo-400">{n}</span>{' '}
      question{n === 1 ? '' : 's'}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Answer value helpers
// ---------------------------------------------------------------------------

type AnswerValue =
  | string
  | string[]
  | QuestionCandidate
  | QuestionCandidate[]
  | null;

/** Compute the default answers object from the question list. */
function buildDefaults(questions: AskUserQuestion[]): Record<string, AnswerValue> {
  const out: Record<string, AnswerValue> = {};
  for (const q of questions) {
    if (q.default === undefined || q.default === null) {
      out[q.id] = q.kind === 'multi_choice' ? [] : null;
    } else if (q.kind === 'freetext') {
      out[q.id] = (q.default as string) ?? '';
    } else if (q.kind === 'single_choice') {
      out[q.id] = (q.default as string) ?? null;
    } else if (q.kind === 'multi_choice') {
      out[q.id] = (q.default as string[]) ?? [];
    } else {
      // catalog_ref / instance_ref, single or multi.
      const d = q.default as QuestionCandidate | QuestionCandidate[] | null;
      if (Array.isArray(d)) out[q.id] = d;
      else if (d) out[q.id] = q.multi ? [d] : d;
      else out[q.id] = q.multi ? [] : null;
    }
  }
  return out;
}

/** Validation error per question id; empty object = all valid. */
function validate(
  questions: AskUserQuestion[],
  answers: Record<string, AnswerValue>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const q of questions) {
    const v = answers[q.id];
    const isEmpty =
      v === null ||
      v === undefined ||
      v === '' ||
      (Array.isArray(v) && v.length === 0);

    if (q.required && isEmpty) {
      errors[q.id] = 'required';
      continue;
    }
    if (q.kind === 'multi_choice') {
      const min = q.min_select ?? 0;
      const max = q.max_select ?? Number.POSITIVE_INFINITY;
      const arr = Array.isArray(v) ? (v as string[]) : [];
      if (arr.length < min) errors[q.id] = 'min_select';
      else if (arr.length > max) errors[q.id] = 'max_select';
    }
  }
  return errors;
}

// ---------------------------------------------------------------------------
// Single-question renderers
// ---------------------------------------------------------------------------

const FreetextQuestion: React.FC<{
  q: Extract<AskUserQuestion, { kind: 'freetext' }>;
  value: string;
  onChange: (v: string) => void;
}> = ({ q, value, onChange }) => {
  const multiline = q.multiline !== false; // default true
  const common = {
    value,
    onChange: (e: React.ChangeEvent<HTMLInputElement> | React.ChangeEvent<HTMLTextAreaElement>) =>
      onChange(e.target.value),
    placeholder: q.placeholder ?? '',
    className:
      'w-full px-2.5 py-1.5 text-xs rounded-xl border border-gray-200 dark:border-dark-border bg-white dark:bg-dark-bg text-gray-800 dark:text-dark-text placeholder-gray-400 dark:placeholder-dark-muted focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-400 dark:focus:border-indigo-500/50 transition-shadow',
  };
  if (multiline) {
    return <textarea {...common} rows={2} />;
  }
  return <input type="text" {...common} />;
};

const SingleChoiceQuestion: React.FC<{
  q: Extract<AskUserQuestion, { kind: 'single_choice' }>;
  value: string | null;
  onChange: (v: string) => void;
}> = ({ q, value, onChange }) => (
  <fieldset className="space-y-1">
    {q.options.map(opt => {
      const checked = value === opt.value;
      return (
        <label
          key={opt.value}
          className={`flex items-start gap-2 px-2.5 py-1.5 rounded-xl border cursor-pointer transition-colors ${
            checked
              ? 'border-indigo-300 dark:border-indigo-500/50 bg-indigo-50/60 dark:bg-indigo-900/20'
              : 'border-gray-200 dark:border-dark-border hover:bg-gray-50 dark:hover:bg-dark-bg/50'
          }`}
        >
          <input
            type="radio"
            name={`q-${q.id}`}
            value={opt.value}
            checked={checked}
            onChange={() => onChange(opt.value)}
            className="mt-0.5 text-indigo-600 focus:ring-indigo-500/40"
          />
          <div className="min-w-0">
            <div className="text-xs font-semibold text-gray-800 dark:text-dark-text">{opt.label}</div>
            {opt.detail && (
              <div className="text-[10px] text-gray-500 dark:text-dark-muted">{opt.detail}</div>
            )}
          </div>
        </label>
      );
    })}
  </fieldset>
);

const MultiChoiceQuestion: React.FC<{
  q: Extract<AskUserQuestion, { kind: 'multi_choice' }>;
  value: string[];
  onChange: (v: string[]) => void;
}> = ({ q, value, onChange }) => {
  const toggle = (val: string) => {
    const next = value.includes(val) ? value.filter(v => v !== val) : [...value, val];
    onChange(next);
  };
  return (
    <fieldset className="space-y-1">
      {q.options.map(opt => {
        const checked = value.includes(opt.value);
        return (
          <label
            key={opt.value}
            className={`flex items-start gap-2 px-2.5 py-1.5 rounded-xl border cursor-pointer transition-colors ${
              checked
                ? 'border-indigo-300 dark:border-indigo-500/50 bg-indigo-50/60 dark:bg-indigo-900/20'
                : 'border-gray-200 dark:border-dark-border hover:bg-gray-50 dark:hover:bg-dark-bg/50'
            }`}
          >
            <input
              type="checkbox"
              checked={checked}
              onChange={() => toggle(opt.value)}
              className="mt-0.5 rounded text-indigo-600 focus:ring-indigo-500/40"
            />
            <div className="min-w-0">
              <div className="text-xs font-semibold text-gray-800 dark:text-dark-text">{opt.label}</div>
              {opt.detail && (
                <div className="text-[10px] text-gray-500 dark:text-dark-muted">{opt.detail}</div>
              )}
            </div>
          </label>
        );
      })}
    </fieldset>
  );
};

/** Backend catalog_type → frontend InstanceType mapping for ``instance_ref``.
 *  Backend uses ``clinical_event`` (the model name); the instance search
 *  dispatcher uses ``event``. */
const INSTANCE_TYPE_MAP: Record<string, InstanceType> = {
  clinical_event: 'event',
  examination: 'examination',
  medication: 'medication',
  observation: 'observation',
  document: 'document',
  allergy: 'allergy',
  vaccine: 'vaccine',
};

/** Convert a QuestionCandidate (the answer shape) from a CatalogSelection. */
function catalogSelectionToCandidate(sel: CatalogSelection): QuestionCandidate {
  return { id: sel.id, name: sel.label, type: sel.type };
}

/** Convert a QuestionCandidate (the answer shape) from an InstanceSelection. */
function instanceSelectionToCandidate(sel: InstanceSelection): QuestionCandidate {
  return {
    id: sel.id,
    name: sel.label ?? sel.subtitle ?? sel.id,
    detail: sel.subtitle,
    type: sel.type,
  };
}

/** Convert the candidate answer back into the CatalogSelection[] shape the
 *  CatalogItemPicker expects (for controlled value round-tripping). */
function candidateToCatalogSelections(
  value: QuestionCandidate[],
): CatalogSelection[] {
  return value.map(c => ({ type: c.type ?? '', id: c.id, label: c.name }));
}

/** Convert the candidate answer back into the InstanceSelection[] shape. */
function candidateToInstanceSelections(
  value: QuestionCandidate[],
): InstanceSelection[] {
  return value.map(c => ({
    type: INSTANCE_TYPE_MAP[c.type ?? ''] ?? 'event',
    id: c.id,
    label: c.name,
    subtitle: c.detail ?? undefined,
  }));
}

/**
 * Build a candidate cache keyed by ``id`` from a question's
 * ``initialCandidates`` snapshot. Used to look up the RICH metadata (code,
 * coding_system, category, is_telemetry, …) for a picked id — without it,
 * the picker's selection would carry only identification fields and the LLM
 * would have to re-fetch every answer.
 *
 * Keyed by ``id`` alone because each question targets ONE catalog/entity
 * type, so ids are unique within a question (UUIDs). Returns a Map; lookups
 * miss gracefully (live-search picks that weren't in the snapshot fall back
 * to identification-only).
 */
function buildCandidateCache(
  candidates: QuestionCandidate[] | null | undefined,
): Map<string, QuestionCandidate> {
  const cache = new Map<string, QuestionCandidate>();
  if (!candidates) return cache;
  for (const c of candidates) {
    cache.set(c.id, c);
  }
  return cache;
}

/**
 * Merge the picker's identification-only selection with the rich metadata
 * from the candidate cache (if the picked id was in the server-side
 * snapshot). Falls back to the identification-only candidate on miss.
 */
function mergeRichCandidates(
  pickedIds: { id: string; fallback: QuestionCandidate }[],
  cache: Map<string, QuestionCandidate>,
): QuestionCandidate[] {
  return pickedIds.map(({ id, fallback }) => cache.get(id) ?? fallback);
}

const RefQuestion: React.FC<{
  q: Extract<AskUserQuestion, { kind: 'catalog_ref' | 'instance_ref' }>;
  /** Answer value — single object for single-select, array for multi-select.
   *  May be null when nothing is picked yet. */
  value: QuestionCandidate | QuestionCandidate[] | null;
  onChange: (v: QuestionCandidate | QuestionCandidate[] | null) => void;
  patientId?: string;
}> = ({ q, value, onChange, patientId }) => {
  const { t } = useTranslation();

  // Cache the server-side candidate snapshot ONCE per question so the rich
  // metadata (code, coding_system, category, is_telemetry, …) is available
  // when the user picks an id. Memoised on the (stable) candidate array.
  const candidateCache = useMemo(
    () => buildCandidateCache(q.candidates),
    [q.candidates],
  );

  // The pickers always operate on arrays internally; normalise the value.
  const valueArr: QuestionCandidate[] = Array.isArray(value)
    ? value
    : value
      ? [value]
      : [];

  if (q.kind === 'catalog_ref') {
    // clinical_event_type is a ConceptKind specialization in the frontend
    // even though the backend treats it as a top-level catalog type.
    const isClinicalEventType = q.catalog_type === 'clinical_event_type';
    const allowedTypes: CatalogType[] = isClinicalEventType
      ? ['concept']
      : ([q.catalog_type] as CatalogType[]);
    const conceptKind = isClinicalEventType ? 'event_category' : undefined;
    const multi = q.multi === true;

    return (
      <CatalogItemPicker
        value={candidateToCatalogSelections(valueArr)}
        onChange={next => {
          const merged = mergeRichCandidates(
            next.map(sel => ({
              id: sel.id,
              fallback: catalogSelectionToCandidate(sel),
            })),
            candidateCache,
          );
          // Single-select answers are stored as a single object (or null),
          // multi-select as an array. Matches the documented answer shape
          // (docs/AI_SYSTEM.md §4.2) and the backend's _stringify_answer.
          onChange(multi ? merged : (merged[0] ?? null));
        }}
        mode={multi ? 'multi' : 'single'}
        allowedTypes={allowedTypes}
        conceptKind={conceptKind}
        placeholder={t('ai_chat.hitl.ask_user.entity_picker.placeholder', {
          defaultValue: 'Search…',
        })}
        block
      />
    );
  }

  // instance_ref
  const instanceType = INSTANCE_TYPE_MAP[q.entity_type] ?? 'event';
  const instMulti = q.multi === true;
  return (
    <InstancePicker
      value={candidateToInstanceSelections(valueArr)}
      onChange={next => {
        const merged = mergeRichCandidates(
          next.map(sel => ({
            id: sel.id,
            fallback: instanceSelectionToCandidate(sel),
          })),
          candidateCache,
        );
        onChange(instMulti ? merged : (merged[0] ?? null));
      }}
      mode={instMulti ? 'multi' : 'single'}
      allowedTypes={[instanceType]}
      patientId={patientId}
      placeholder={t('ai_chat.hitl.ask_user.entity_picker.placeholder', {
        defaultValue: 'Search…',
      })}
      block
    />
  );
};

const UnsupportedQuestion: React.FC<{ kind: string }> = ({ kind }) => {
  const { t } = useTranslation();
  return (
    <div className="rounded-xl border border-amber-200 dark:border-amber-500/30 bg-amber-50/60 dark:bg-amber-900/10 px-2.5 py-1.5 text-[10px] text-amber-700 dark:text-amber-300">
      {t('ai_chat.hitl.ask_user.unsupported_kind', {
        defaultValue: 'Unsupported question type: {{kind}}',
        kind,
      })}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Question router
// ---------------------------------------------------------------------------

const QuestionRouter: React.FC<{
  q: AskUserQuestion;
  value: AnswerValue;
  onChange: (v: AnswerValue) => void;
  patientId?: string;
}> = ({ q, value, onChange, patientId }) => {
  switch (q.kind) {
    case 'freetext':
      return (
        <FreetextQuestion
          q={q}
          value={(value as string) ?? ''}
          onChange={v => onChange(v)}
        />
      );
    case 'single_choice':
      return (
        <SingleChoiceQuestion
          q={q}
          value={(value as string | null) ?? null}
          onChange={v => onChange(v)}
        />
      );
    case 'multi_choice':
      return (
        <MultiChoiceQuestion
          q={q}
          value={Array.isArray(value) ? (value as string[]) : []}
          onChange={v => onChange(v)}
        />
      );
    case 'catalog_ref':
    case 'instance_ref':
      return (
        <RefQuestion
          q={q}
          value={(value as QuestionCandidate | QuestionCandidate[] | null) ?? null}
          onChange={v => onChange(v)}
          patientId={patientId}
        />
      );
    default:
      return <UnsupportedQuestion kind={(q as { kind?: string }).kind ?? 'unknown'} />;
  }
};

// ---------------------------------------------------------------------------
// Main handler
// ---------------------------------------------------------------------------

export const AskUserHandler: React.FC<HitlHandlerProps> = ({
  task,
  sessionId,
  onResolved,
}) => {
  const { t } = useTranslation();
  const payload = (task.proposed_payload || {}) as Partial<AskUserPayload>;
  const questions = useMemo(() => payload.questions ?? [], [payload.questions]);
  const patientId = task.context?.patient_id as string | undefined;

  const [answers, setAnswers] = useState<Record<string, AnswerValue>>(() =>
    buildDefaults(questions),
  );
  const [submitting, setSubmitting] = useState<false | 'submit' | 'skip'>(false);
  const [error, setError] = useState<string | null>(null);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  const errors = useMemo(() => validate(questions, answers), [questions, answers]);

  const allValid = Object.keys(errors).length === 0;
  const unansweredCount = Object.values(answers).filter(v =>
    v === null ||
    v === undefined ||
    v === '' ||
    (Array.isArray(v) && v.length === 0),
  ).length;

  const setOne = (id: string, v: AnswerValue) => {
    setAnswers(prev => ({ ...prev, [id]: v }));
    // Clear field-level error as the user edits.
    if (validationErrors[id]) {
      setValidationErrors(prev => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  };

  const handleSubmit = async () => {
    if (!allValid) {
      setValidationErrors(errors);
      return;
    }
    setSubmitting('submit');
    setError(null);
    try {
      // Strip empty-string answers to null so the LLM sees a clean signal.
      const cleaned: AskUserAnswers = {};
      for (const q of questions) {
        const v = answers[q.id];
        cleaned[q.id] = v === '' ? null : v;
      }
      if (sessionId) {
        try {
          await resolveHitlTask(sessionId, task.proposal_id, {
            status: 'confirmed',
            final_payload: { answers: cleaned },
          });
        } catch (resolveErr) {
          // No write happened (ask_user is read-only) — surface the error so
          // the user can retry. Do not auto-dismiss the card.
          console.error('ask_user resolve failed', resolveErr);
          throw resolveErr;
        }
      }
      onResolved({
        ...task,
        status: 'confirmed',
        resolved: {
          final_payload: { answers: cleaned },
          at: new Date().toISOString(),
        },
      });
    } catch (e: any) {
      const msg =
        e?.response?.data?.detail ||
        e?.message ||
        String(t('ai_chat.hitl.error_generic', 'Failed to save. Please review and try again.'));
      setError(typeof msg === 'string' ? msg : JSON.stringify(msg));
    } finally {
      setSubmitting(false);
    }
  };

  const handleSkip = async () => {
    setSubmitting('skip');
    setError(null);
    try {
      if (sessionId) {
        try {
          await resolveHitlTask(sessionId, task.proposal_id, { status: 'dismissed' });
        } catch (resolveErr) {
          console.error('ask_user skip record failed', resolveErr);
        }
      }
      onResolved({
        ...task,
        status: 'dismissed',
        resolved: { at: new Date().toISOString() },
      });
    } finally {
      setSubmitting(false);
    }
  };

  if (!questions.length) {
    return (
      <div className="rounded-xl border border-amber-200 dark:border-amber-500/30 bg-amber-50/60 dark:bg-amber-900/10 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300">
        {t('ai_chat.hitl.ask_user.empty', {
          defaultValue: 'The assistant did not include any questions.',
        })}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 min-w-0">
      {payload.summary && (
        <p className="text-[11px] text-gray-600 dark:text-dark-muted leading-relaxed">
          {payload.summary}
        </p>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-200 dark:border-rose-500/30 bg-rose-50 dark:bg-rose-900/10 p-2.5 text-[11px] text-rose-700 dark:text-rose-300">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span className="break-words">{error}</span>
        </div>
      )}

      <div className="space-y-3">
        {questions.map(q => {
          const fieldErr = validationErrors[q.id];
          return (
            <div key={q.id} className="space-y-1">
              <label
                htmlFor={`ask-user-${q.id}`}
                className="block text-xs font-bold text-gray-800 dark:text-dark-text"
              >
                {q.prompt}
                {q.required && (
                  <span className="ml-1 text-rose-500" aria-hidden>*</span>
                )}
              </label>
              {q.help_text && (
                <p className="text-[10px] text-gray-500 dark:text-dark-muted">
                  {q.help_text}
                </p>
              )}
              <QuestionRouter
                q={q}
                value={answers[q.id] ?? null}
                onChange={v => setOne(q.id, v)}
                patientId={patientId}
              />
              {fieldErr && (
                <p className="text-[10px] text-rose-600 dark:text-rose-400 font-semibold">
                  {t(`ai_chat.hitl.ask_user.error.${fieldErr}`, {
                    defaultValue: 'This field needs attention.',
                  })}
                </p>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex items-center justify-between gap-2 pt-1">
        <button
          type="button"
          onClick={handleSkip}
          disabled={submitting !== false}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-[11px] font-bold text-gray-600 dark:text-dark-muted hover:bg-gray-100 dark:hover:bg-dark-bg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <SkipForward className="w-3.5 h-3.5" />
          <span>{t('ai_chat.hitl.ask_user.skip', 'Skip questions')}</span>
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={submitting !== false || !allValid}
          className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-xl text-[11px] font-black text-white bg-indigo-600 hover:bg-indigo-700 shadow-lg shadow-indigo-500/20 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {submitting === 'submit' ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Send className="w-3.5 h-3.5" />
          )}
          <span>
            {t('ai_chat.hitl.ask_user.submit', {
              defaultValue: 'Submit answers',
              count: unansweredCount,
            })}
          </span>
        </button>
      </div>
    </div>
  );
};

export default AskUserHandler;
