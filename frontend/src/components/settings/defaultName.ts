/**
 * Connection-name auto-seed (family provider-settings contract): the
 * engine label, numbered when taken — "Groq", "Groq 2", "Groq 3" …
 * App-side, which owns the instances (the family UI standard, provider-settings).
 */
export function defaultName(base: string, takenNames: string[]): string {
  const taken = new Set(takenNames);
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base} ${n}`)) n += 1;
  return `${base} ${n}`;
}
