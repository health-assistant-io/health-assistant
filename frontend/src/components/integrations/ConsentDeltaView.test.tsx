import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ConsentDeltaView, { type ConsentDelta } from './ConsentDeltaView';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string | null, options?: Record<string, unknown>) => {
      const template = typeof fallback === 'string' ? fallback : key;
      if (!options) return template;
      return template.replace(/{{(\w+)}}/g, (_, name: string) => String(options[name] ?? ''));
    },
  }),
}));

const upgradeDelta: ConsentDelta = {
  versionId: 'v-2',
  incomingDigest: 'a'.repeat(64),
  runningDigest: 'b'.repeat(64),
  version: { from: '1.0.0', to: '1.1.0' },
  capabilitiesAdded: ['push'],
  capabilitiesRemoved: [],
  capabilitiesUnchanged: ['sync'],
  keptGrants: ['sync'],
  requiresReConsent: true,
  status: 'approved',
  disclosureKey: 't3PerimeterDisclosure',
  disclosure: 'This extension runs as a separate process outside the app\'s network perimeter.',
};

const installDelta: ConsentDelta = {
  ...upgradeDelta,
  version: { from: null, to: '1.0.0' },
  capabilitiesAdded: ['sync', 'push'],
  capabilitiesUnchanged: [],
  keptGrants: [],
  runningDigest: null,
};

describe('ConsentDeltaView (8C change-summary)', () => {
  it('forces a decision per new capability before submit enables', () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    render(<ConsentDeltaView delta={upgradeDelta} onDecide={onDecide} />);

    const added = screen.getByTestId('consent-added-list');
    expect(added.textContent).toContain('push');
    const submit = screen.getByTestId('consent-submit') as HTMLButtonElement;
    expect(submit.disabled, 'nothing decided yet').toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
    expect(submit.disabled).toBe(false);
  });

  it('shows kept grants for unchanged capabilities and never re-prompts them', () => {
    render(<ConsentDeltaView delta={upgradeDelta} onDecide={vi.fn()} />);
    const kept = screen.getByTestId('consent-kept-sync');
    expect(kept.textContent).toContain('sync');
    expect(screen.queryByTestId('consent-capability-sync')).toBeNull();
  });

  it('submits exactly the decided set and nothing else', async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    render(<ConsentDeltaView delta={installDelta} onDecide={onDecide} />);

    fireEvent.click(screen.getByTestId('consent-capability-sync').querySelector('button')!);
    fireEvent.click(
      screen
        .getByTestId('consent-capability-push')
        .querySelectorAll('button')[1],
    );

    fireEvent.click(screen.getByTestId('consent-submit'));
    await waitFor(() => expect(onDecide).toHaveBeenCalledTimes(1));
    expect(onDecide).toHaveBeenCalledWith('v-2', { sync: 'granted', push: 'denied' });
  });

  it('renders the charter disclosure verbatim, keyed by disclosureKey', () => {
    render(<ConsentDeltaView delta={upgradeDelta} onDecide={vi.fn()} />);
    const block = screen.getByTestId('consent-disclosure');
    expect(block.getAttribute('data-disclosure-key')).toBe('t3PerimeterDisclosure');
    expect(block.textContent).toBe(upgradeDelta.disclosure);
  });

  it('reports removed capabilities without re-prompting', () => {
    const delta: ConsentDelta = {
      ...upgradeDelta,
      capabilitiesAdded: [],
      capabilitiesRemoved: ['notify'],
      capabilitiesUnchanged: ['sync'],
      requiresReConsent: false,
    };
    render(<ConsentDeltaView delta={delta} onDecide={vi.fn()} />);
    expect(screen.getByTestId('consent-removed-list').textContent).toContain('notify');
    expect(screen.queryByTestId('consent-added-list')).toBeNull();
    const submit = screen.getByTestId('consent-submit') as HTMLButtonElement;
    expect(submit.disabled, 'nothing new to decide').toBe(true);
  });
});
