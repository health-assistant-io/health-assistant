/**
 * Extract a readable message from an axios/API error for toasts and inline
 * error rows.
 *
 * FastAPI validation errors arrive as a 422 whose `detail` is an **array** of
 * `{loc, msg, type}` objects — rendering `detail` directly (or falling back to
 * the generic message when it is not a string) hides the actual problem (e.g.
 * "slug: Field required"). This picks the first message, prefixes the field
 * path when one exists, passes plain string details through, and returns null
 * when the error carries no usable backend message (the caller shows its
 * generic fallback).
 */
export function apiErrorDetail(err: unknown): string | null {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response
    ?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { loc?: unknown; msg?: unknown };
    const msg = typeof first?.msg === 'string' && first.msg.trim() ? first.msg : null;
    const field = Array.isArray(first?.loc)
      ? (first.loc as unknown[])
          .filter((p) => typeof p === 'string' && p !== 'body')
          .join('.')
      : null;
    if (field && msg) return `${field}: ${msg}`;
    return msg;
  }
  if (
    detail &&
    typeof detail === 'object' &&
    typeof (detail as { msg?: unknown }).msg === 'string' &&
    (detail as { msg: string }).msg.trim()
  ) {
    return (detail as { msg: string }).msg;
  }
  return null;
}
