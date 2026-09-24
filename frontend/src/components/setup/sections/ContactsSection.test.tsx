import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ContactsSection } from './ContactsSection';
import type { Patient } from '../../../types/patient';

vi.mock('../../../services/patientService', () => ({
  updatePatient: vi.fn(),
}));

import { updatePatient } from '../../../services/patientService';

const basePatient: Patient = {
  id: 'p-1',
  tenant_id: 't-1',
  name: { family: 'Doe', given: ['John'] },
  gender: 'male',
  extensions: null,
};

describe('ContactsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (updatePatient as any).mockResolvedValue({});
  });

  it('serializes Address.line as list[str] on save (FHIR R4)', async () => {
    render(<ContactsSection patient={basePatient} />);

    const streetInput = screen.getByPlaceholderText('123 Main St') as HTMLInputElement;
    fireEvent.change(streetInput, { target: { value: '123 Main St' } });

    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(updatePatient).toHaveBeenCalled());
    const [, payload] = (updatePatient as any).mock.calls[0];
    expect(Array.isArray(payload.address)).toBe(true);
    expect(payload.address[0].line).toEqual(['123 Main St']);
  });

  it('splits a multi-line street into separate Address.line entries', async () => {
    render(<ContactsSection patient={basePatient} />);

    const streetInput = screen.getByPlaceholderText('123 Main St') as HTMLInputElement;
    fireEvent.change(streetInput, { target: { value: 'c/o Someone\n123 Main St' } });

    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(updatePatient).toHaveBeenCalled());
    const [, payload] = (updatePatient as any).mock.calls[0];
    expect(payload.address[0].line).toEqual(['c/o Someone', '123 Main St']);
  });

  it('hydrates an existing list-shaped Address.line into the single-line input', () => {
    const patient: Patient = {
      ...basePatient,
      address: [{ line: ['c/o Someone', '123 Main St'], city: 'Springfield', postalCode: '00000', country: 'US' }],
    } as any;
    render(<ContactsSection patient={patient} />);
    const streetInput = screen.getByPlaceholderText('123 Main St') as HTMLInputElement;
    expect(streetInput.value).toBe('c/o Someone\n123 Main St');
  });

  it('surfaces a backend Pydantic 422 error message instead of failing silently', async () => {
    (updatePatient as any).mockRejectedValueOnce({
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'address', 0, 'line'], msg: 'Input should be a valid list', type: 'list_type' },
          ],
        },
      },
    });

    render(<ContactsSection patient={basePatient} />);
    fireEvent.change(screen.getByPlaceholderText('123 Main St'), { target: { value: 'x' } });
    fireEvent.click(screen.getByText('Save'));

    expect(await screen.findByText(/Input should be a valid list/)).toBeInTheDocument();
  });
});
