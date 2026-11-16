import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AddSourceWizard } from '../AddSourceWizard';
import { createSeedSource } from '../../../../services/seedSourcesService';

vi.mock('react-i18next', () => {
  const t = (key: string, opts?: unknown) => {
    if (typeof opts === 'string') return opts;
    if (opts && typeof opts === 'object' && 'defaultValue' in (opts as object)) {
      return String((opts as { defaultValue: string }).defaultValue);
    }
    return key;
  };
  return { useTranslation: () => ({ t, i18n: { language: 'en' } }) };
});

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('../../../../services/seedSourcesService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../../services/seedSourcesService')
  >();
  return {
    ...actual,
    createSeedSource: vi.fn(async () => ({ id: 'src-new' }) as never),
  };
});

const mockedCreate = vi.mocked(createSeedSource);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AddSourceWizard', () => {
  it('offers the secret slot for git and registry sources, not for file', async () => {
    const user = userEvent.setup();
    render(<AddSourceWizard open onOpenChange={() => {}} canChooseScope onCreated={() => {}} />);

    expect(screen.queryByLabelText(/Secret slot/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'git' }));
    expect(screen.getByLabelText(/Secret slot/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'registry' }));
    expect(screen.getByLabelText(/Secret slot/)).toBeInTheDocument();
  });

  it('sends auth_secret_ref for a registry source', async () => {
    const user = userEvent.setup();
    render(<AddSourceWizard open onOpenChange={() => {}} canChooseScope onCreated={() => {}} />);
    await user.click(screen.getByRole('button', { name: 'registry' }));
    await user.type(screen.getByLabelText(/URL/), 'https://registry.example.com/pack.zip');
    await user.type(screen.getByLabelText(/Secret slot/), 'registry-read');
    await user.click(screen.getByRole('button', { name: 'Register source' }));
    await vi.waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: 'registry',
          uri: 'https://registry.example.com/pack.zip',
          auth_secret_ref: 'registry-read',
        }),
      ),
    );
  });

  it('defaults the priority into the scope band when the scope flips', async () => {
    const user = userEvent.setup();
    render(<AddSourceWizard open onOpenChange={() => {}} canChooseScope onCreated={() => {}} />);

    expect(screen.getByLabelText('Priority')).toHaveValue(100);
    await user.click(screen.getByRole('button', { name: 'tenant' }));
    expect(screen.getByLabelText('Priority')).toHaveValue(250);
    await user.click(screen.getByRole('button', { name: 'system' }));
    expect(screen.getByLabelText('Priority')).toHaveValue(100);
  });

  it('keeps a typed priority when the scope flips', async () => {
    const user = userEvent.setup();
    render(<AddSourceWizard open onOpenChange={() => {}} canChooseScope onCreated={() => {}} />);
    await user.clear(screen.getByLabelText('Priority'));
    await user.type(screen.getByLabelText('Priority'), '42');
    await user.click(screen.getByRole('button', { name: 'tenant' }));
    expect(screen.getByLabelText('Priority')).toHaveValue(42);
  });

  it('defaults a tenant admin into the tenant band when the scope is not choosable', async () => {
    const user = userEvent.setup();
    render(
      <AddSourceWizard
        open
        onOpenChange={() => {}}
        canChooseScope={false}
        onCreated={() => {}}
      />,
    );

    expect(screen.getByLabelText('Priority')).toHaveValue(250);
    expect(screen.getByText('tenant band: 200–999')).toBeInTheDocument();
    expect(screen.queryByText('system band: 0–199 (bundled pins 0)')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'tenant' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Register source' }));
    await vi.waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'file', priority: 250 }),
      ),
    );
    expect(mockedCreate.mock.calls[0][0]).not.toHaveProperty('scope');
  });
});
