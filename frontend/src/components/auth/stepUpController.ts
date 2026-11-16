/**
 * The bridge between the axios interceptor (no React) and the step-up
 * modal (React). The mounted `StepUpProvider` registers a presenter; the
 * interceptor awaits its answer before replaying the failed request.
 *
 * Challenges are serialized: two gated requests in flight show one modal at
 * a time, and a second challenge for the same action shares the first
 * promise (no duplicate prompts, no retry storm).
 */
import type { StepUpChallenge } from '../../services/stepUpTypes';

export type StepUpPresenter = (challenge: StepUpChallenge) => Promise<boolean>;

let presenter: StepUpPresenter | null = null;
let queue: Promise<unknown> = Promise.resolve();
const inFlight = new Map<string, Promise<boolean>>();

export function registerStepUpPresenter(next: StepUpPresenter | null): void {
  presenter = next;
  if (!next) {
    inFlight.clear();
  }
}

/** Ask the UI to satisfy `challenge`; false when no UI is mounted or the
 * user cancels. Never rejects. */
export function resolveStepUp(challenge: StepUpChallenge): Promise<boolean> {
  if (!presenter) return Promise.resolve(false);

  const key = `${challenge.code}:${challenge.action}`;
  const existing = inFlight.get(key);
  if (existing) return existing;

  const run = queue.then(async () => {
    if (!presenter) return false;
    try {
      return await presenter(challenge);
    } catch {
      return false;
    }
  });
  queue = run.catch(() => false);
  inFlight.set(key, run);
  void run.finally(() => {
    if (inFlight.get(key) === run) inFlight.delete(key);
  });
  return run;
}

/** Test hook: drop the presenter and pending serialization. */
export function resetStepUpController(): void {
  presenter = null;
  queue = Promise.resolve();
  inFlight.clear();
}
