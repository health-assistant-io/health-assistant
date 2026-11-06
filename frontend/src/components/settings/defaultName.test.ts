import { describe, expect, it } from 'vitest';

import { defaultName } from './defaultName';

describe('defaultName', () => {
  it('returns the base label when it is not taken', () => {
    expect(defaultName('Groq', ['OpenAI Main'])).toBe('Groq');
  });

  it('returns the base when the taken list is empty', () => {
    expect(defaultName('Ollama', [])).toBe('Ollama');
  });

  it('numbers the second instance', () => {
    expect(defaultName('Groq', ['OpenAI', 'Groq'])).toBe('Groq 2');
  });

  it('skips already-numbered names', () => {
    expect(defaultName('Groq', ['Groq', 'Groq 2', 'Groq 3'])).toBe('Groq 4');
  });

  it('does not collide with a same-prefix different-name provider', () => {
    expect(defaultName('Groq', ['Groqify'])).toBe('Groq');
  });
});
