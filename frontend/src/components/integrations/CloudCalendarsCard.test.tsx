import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import CloudCalendarsCard from './CloudCalendarsCard';

const get = vi.fn();
const post = vi.fn();
const del = vi.fn();

vi.mock('../../api/axios', () => ({
  default: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
    delete: (...args: unknown[]) => del(...args),
  },
}));

vi.mock('../../store/slices/patientSlice', () => {
  // one stable patient object — a fresh object per render would retrigger
  // every effect that depends on it, forever
  const patient = { id: 'pat-1' };
  return { usePatientStore: () => ({ currentPatient: patient }) };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));

const CATALOG = {
  providers: [
    {
      id: 'nextcloud',
      label: 'Nextcloud',
      authKinds: ['app-password'],
      baseUrlRequired: true,
      protocol: 'caldav',
      writable: true,
      supportsDelta: false,
      scopePresets: { read: [], write: [] },
      consentNotice: 'This app stores an app-specific password you create at Nextcloud.',
      consentNotices: {
        'app-password': 'This app stores an app-specific password you create at Nextcloud.',
      },
      domains: [],
      setupFields: [
        { key: 'calendar_url', kind: 'url', required: true, labelKey: 'calendarUrl', authKind: null },
        { key: 'calendar', kind: 'calendar', required: false, labelKey: 'calendarPicker', authKind: null },
        { key: 'username', kind: 'text', required: true, labelKey: 'username', authKind: 'app-password' },
        { key: 'secret', kind: 'secret', required: true, labelKey: 'appPassword', authKind: 'app-password' },
        { key: 'secret', kind: 'secret', required: true, labelKey: 'token', authKind: 'token' },
      ],
    },
    {
      id: 'ics',
      label: 'ICS feed',
      authKinds: ['none', 'token'],
      baseUrlRequired: false,
      protocol: 'ics',
      writable: false,
      supportsDelta: false,
      scopePresets: { read: [], write: [] },
      consentNotice: 'This source needs no credential; it only reads a public feed.',
      consentNotices: {
        none: 'This source needs no credential; it only reads a public feed.',
        token: 'This app stores an access token for ICS feed. Revoking it at ICS feed disconnects this app.',
      },
      domains: [],
    },
  ],
  redactionProfiles: [
    { id: 'neutral', description: 'Publishes “Medical appointment” — no detail.' },
    { id: 'full', description: 'Publishes real detail. Opt-in.' },
  ],
  redactionDefault: 'neutral',
  directions: ['push', 'pull', 'two-way'],
  accountStates: ['ok', 'needs-auth'],
};

const BINDING = {
  id: 'b-1',
  provider: 'nextcloud',
  auth_kind: 'app-password',
  label: 'Family calendar',
  account_label: null,
  direction: 'push',
  redaction: 'neutral',
  enabled: true,
  status: 'ok',
  last_error: null,
  last_synced_at: null,
  consecutive_failures: 0,
  options: {},
};

describe('CloudCalendarsCard', () => {
  beforeEach(() => {
    get.mockReset().mockImplementation((url: string) =>
      Promise.resolve({
        data: url.includes('/providers') ? CATALOG : [BINDING],
      }),
    );
    post.mockReset().mockResolvedValue({ data: {} });
    del.mockReset().mockResolvedValue({ data: {} });
  });

  it('renders the catalog-driven bind surface and the bound account status', async () => {
    const user = userEvent.setup();
    render(<CloudCalendarsCard />);
    expect(await screen.findByText('Cloud calendars')).toBeTruthy();
    expect(await screen.findByText(/Family calendar/)).toBeTruthy();
    expect(screen.getByText(/redaction neutral/)).toBeTruthy();
    // the redaction description is catalog data, rendered in the bind form
    await user.click(screen.getByRole('button', { name: /bind a calendar/i }));
    expect(await screen.findByText(/no detail/)).toBeTruthy();
  });

  it('opens the bind form with the provider fields', async () => {
    render(<CloudCalendarsCard />);
    const button = await screen.findByRole('button', { name: /bind a calendar/i });
    await userEvent.click(button);
    expect(await screen.findByText(/Calendar URL \(CalDAV collection\)/)).toBeTruthy();
    expect(screen.getByText(/App password/)).toBeTruthy();
    // the notice follows the (default) chosen auth kind
    expect(screen.getByText(/app-specific password/)).toBeTruthy();
  });

  it('keys the consent notice to the CHOSEN auth kind (E12)', async () => {
    const user = userEvent.setup();
    render(<CloudCalendarsCard />);
    const button = await screen.findByRole('button', { name: /bind a calendar/i });
    await user.click(button);
    // switch to the ICS provider: the credential-capable kind is the default,
    // so the consent copy names the access token — never "needs no credential"
    const providerSelect = await screen.findByDisplayValue('Nextcloud');
    await user.selectOptions(providerSelect, 'ics');
    expect(await screen.findByText(/access token for ICS feed/i)).toBeTruthy();
    // switch the kind: the notice follows the CHOSEN kind verbatim
    await user.selectOptions(screen.getByLabelText(/Authentication/), 'none');
    expect(await screen.findByText(/needs no credential/)).toBeTruthy();
    await user.selectOptions(screen.getByLabelText(/Authentication/), 'token');
    expect(await screen.findByText(/access token for ICS feed/i)).toBeTruthy();
    expect(screen.queryByText(/needs no credential/)).toBeNull();
  });
});
