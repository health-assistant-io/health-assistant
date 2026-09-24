import { describe, expect, it } from 'vitest';

import {
  formatPatientSaveError,
  toPatientAddressFormValues,
  toPatientAddresses,
} from './patientForm';

describe('toPatientAddresses', () => {
  it('round-trips a stored address, including elements the form does not edit', () => {
    const stored = [
      {
        use: 'home',
        type: 'both',
        line: ['c/o Someone', '123 Main St'],
        city: 'Springfield',
        district: 'Sangamon',
        state: 'IL',
        postalCode: '62701',
        country: 'US',
        period: { start: '2020-01-01' },
        extension: [{ url: 'urn:example', valueString: 'x' }],
      },
    ];

    expect(toPatientAddresses(toPatientAddressFormValues(stored))).toEqual(stored);
  });

  it('trims values and drops empty fields and rows', () => {
    expect(
      toPatientAddresses([
        { line: '  123 Main St \n\n', city: '  ', postalCode: ' 62701 ', country: '' },
        { line: '', city: '', text: '   ' },
      ]),
    ).toEqual([{ line: ['123 Main St'], postalCode: '62701' }]);
  });

  it('keeps a row whose only content is an element the form does not edit', () => {
    expect(toPatientAddresses([{ line: '', state: 'IL' }])).toEqual([{ state: 'IL' }]);
  });
});

describe('formatPatientSaveError', () => {
  it('uses a string detail as is', () => {
    const error = { response: { status: 400, data: { detail: 'address.0.line: Input should be a valid list' } } };
    expect(formatPatientSaveError(error, 'Failed')).toBe('address.0.line: Input should be a valid list');
  });

  it('formats the first entry of a validation-error list', () => {
    const error = {
      response: { status: 422, data: { detail: [{ loc: ['body', 'name'], msg: 'Field required' }] } },
    };
    expect(formatPatientSaveError(error, 'Failed')).toBe('body.name: Field required');
  });

  it('falls back to the status, then the message, then the fallback', () => {
    expect(formatPatientSaveError({ response: { status: 503, statusText: 'Service Unavailable' } }, 'Failed')).toBe(
      '503: Service Unavailable',
    );
    expect(formatPatientSaveError(new Error('Network Error'), 'Failed')).toBe('Network Error');
    expect(formatPatientSaveError(null, 'Failed')).toBe('Failed');
  });
});
