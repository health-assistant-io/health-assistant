/**
 * apiErrorDetail — readable messages out of axios/API errors.
 *
 * Pins the FastAPI 422 shape: an array `detail` (validation errors) yields the
 * FIRST message with the field path (`body.slug` → `slug`); plain string
 * details pass through; anything else (network errors, missing body) → null so
 * the caller keeps its generic fallback.
 */
import { describe, it, expect } from 'vitest';
import { apiErrorDetail } from '../apiErrors';

describe('apiErrorDetail', () => {
  it('passes a string detail through', () => {
    expect(
      apiErrorDetail({ response: { data: { detail: 'name already exists' } } }),
    ).toBe('name already exists');
  });

  it('renders the first message of a 422 array detail with the field path', () => {
    const err = {
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'slug'], msg: 'Field required', type: 'missing' },
            { loc: ['body', 'name'], msg: 'Field required', type: 'missing' },
          ],
        },
      },
    };
    expect(apiErrorDetail(err)).toBe('slug: Field required');
  });

  it('drops the body segment and non-string path entries from the location', () => {
    const err = {
      response: {
        data: { detail: [{ loc: ['body', 0, 'code'], msg: 'bad code' }] },
      },
    };
    expect(apiErrorDetail(err)).toBe('code: bad code');
  });

  it('returns the bare message when the array entry has no location', () => {
    expect(
      apiErrorDetail({ response: { data: { detail: [{ msg: 'nope' }] } } }),
    ).toBe('nope');
  });

  it('reads msg off an object detail', () => {
    expect(
      apiErrorDetail({ response: { data: { detail: { msg: 'object detail' } } } }),
    ).toBe('object detail');
  });

  it('returns null when there is no usable detail (caller shows the generic)', () => {
    expect(apiErrorDetail(new Error('network down'))).toBeNull();
    expect(apiErrorDetail(undefined)).toBeNull();
    expect(apiErrorDetail({ response: { data: {} } })).toBeNull();
    expect(apiErrorDetail({ response: { data: { detail: [] } } })).toBeNull();
  });
});
