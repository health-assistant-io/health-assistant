import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';

/**
 * URL-based "create" intent hook.
 *
 * Convention: a CreateMenu (or any link) navigates to a list/detail page with
 * `?new=<intentId>` to signal that the page should auto-open its create modal.
 * The hook watches for that param, fires the callback exactly once, then
 * strips the param from the URL so reload / back-button doesn't re-trigger.
 *
 * Usage:
 *   // Default: matches ?new=true
 *   useCreateIntent(() => setModalOpen(true));
 *
 *   // Match a specific id: ?new=medication
 *   useCreateIntent(() => setModalOpen(true), 'medication');
 *
 * @param onOpenCreate  Fired once when the intent is present.
 * @param intentId      Value to match against `?new=<intentId>`. Defaults to 'true'.
 * @param deps          Extra deps that should re-trigger the check (e.g. data-loading flags).
 */
export function useCreateIntent(
  onOpenCreate: () => void,
  intentId: string = 'true',
  deps: ReadonlyArray<unknown> = []
): void {
  const [searchParams, setSearchParams] = useSearchParams();
  const firedRef = useRef(false);
  const cbRef = useRef(onOpenCreate);
  cbRef.current = onOpenCreate;

  useEffect(() => {
    const current = searchParams.get('new');

    if (current === intentId && !firedRef.current) {
      firedRef.current = true;
      // Strip the param so it doesn't refire on remount / back-button
      const next = new URLSearchParams(searchParams);
      next.delete('new');
      setSearchParams(next, { replace: true });
      cbRef.current();
    }

    // Reset the latch whenever the intent is absent so a fresh `?new=...` later
    // in the same mount triggers again.
    if (current !== intentId) {
      firedRef.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, intentId, ...deps]);
}

export default useCreateIntent;
