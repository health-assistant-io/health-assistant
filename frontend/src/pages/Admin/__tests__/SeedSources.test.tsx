import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useAccessStore } from '../../../store/slices/accessSlice';
import { useUIStore } from '../../../store/slices/uiSlice';

vi.mock('react-i18next', () => {
  const t = (key: string, opts?: unknown) => {
    if (typeof opts === 'string') return opts;
    if (opts && typeof opts === 'object' && 'defaultValue' in (opts as object)) {
      return String((opts as { defaultValue: string }).defaultValue).replace(
        /\{\{(\w+)\}\}/g,
        (_m, name: string) => String((opts as Record<string, unknown>)[name] ?? `{{${name}}}`),
      );
    }
    return key;
  };
  return { useTranslation: () => ({ t, i18n: { language: 'en' } }) };
});

vi.mock('react-toastify', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('../../../services/seedService', () => ({
  downloadSeedsZip: vi.fn(async () => undefined),
}));

import SeedSources from '../SeedSources';
import {
  approveSeedVersion,
  adoptSeedUpdates,
  listSeedSources,
  planSeedVersion,
  retireSeedSource,
  revokeSeedVersion,
  syncSeedSource,
  type SeedPlanDoc,
  type SeedSource,
  type SeedSourceVersion,
} from '../../../services/seedSourcesService';

vi.mock('../../../services/seedSourcesService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/seedSourcesService')>();
  return {
    ...actual,
    listSeedSources: vi.fn(),
    syncSeedSource: vi.fn(),
    retireSeedSource: vi.fn(),
    revokeSeedVersion: vi.fn(),
    approveSeedVersion: vi.fn(),
    adoptSeedUpdates: vi.fn(),
    planSeedVersion: vi.fn(),
  };
});

const mocked = {
  listSeedSources: vi.mocked(listSeedSources),
  syncSeedSource: vi.mocked(syncSeedSource),
  retireSeedSource: vi.mocked(retireSeedSource),
  revokeSeedVersion: vi.mocked(revokeSeedVersion),
  approveSeedVersion: vi.mocked(approveSeedVersion),
  adoptSeedUpdates: vi.mocked(adoptSeedUpdates),
  planSeedVersion: vi.mocked(planSeedVersion),
};

function sourceRow(overrides: Partial<SeedSource> = {}): SeedSource {
  return {
    id: 'src-1',
    tenant_id: null,
    kind: 'git',
    uri: 'https://github.com/org/catalog-packs.git',
    ref_spec: 'v1.0.0',
    subpath: 'packs/core',
    priority: 100,
    scope: 'system',
    enabled: true,
    auth_secret_ref: 'github-read',
    current_version_id: 'ver-1',
    last_check_at: '2026-11-09T10:00:00Z',
    last_error: null,
    created_at: '2026-11-09T09:00:00Z',
    updated_at: '2026-11-09T10:00:00Z',
    versions: [
      {
        id: 'ver-1',
        source_id: 'src-1',
        digest: 'a'.repeat(64),
        commit_sha: 'abc1234',
        etag: null,
        status: 'pending',
        reason: null,
        approved_by: null,
        approved_at: null,
        contract_version: '1.0',
        created_at: '2026-11-09T10:00:00Z',
        updated_at: '2026-11-09T10:00:00Z',
        pack_id: 'acme-catalog',
        pack_version: '1.2.0',
        pack_scope: 'system',
        pack_priority: 100,
        pack_files: 12,
        is_current: false,
        latest_run: null,
      },
    ],
    ...overrides,
  };
}

function versionRow(id: string, overrides: Partial<SeedSourceVersion> = {}): SeedSourceVersion {
  return {
    id,
    source_id: 'src-1',
    digest: 'b'.repeat(64),
    commit_sha: null,
    etag: null,
    status: 'pending',
    reason: null,
    approved_by: null,
    approved_at: null,
    contract_version: '1.0',
    created_at: '2026-11-09T10:00:00Z',
    updated_at: '2026-11-09T10:00:00Z',
    pack_id: 'acme-catalog',
    pack_version: '1.2.0',
    pack_scope: 'system',
    pack_priority: 100,
    pack_files: 12,
    is_current: false,
    latest_run: null,
    ...overrides,
  };
}

function planDoc(): SeedPlanDoc {
  return {
    pack: { id: 'acme-catalog', version: '1.2.0', digest: 'a'.repeat(64), scope: 'system', priority: 100 },
    source: { id: 'src-1', kind: 'git' },
    stages: {
      medications: [
        {
          stage: 'medications',
          entity_type: 'medication',
          seed_key: 'medication:aspirin',
          entity_id: null,
          action: 'skip',
          content_hash: 'h1',
          update_available: true,
          conflict: { reason: 'user_modified_freeze' },
          warnings: [],
        },
        {
          stage: 'medications',
          entity_type: 'medication',
          seed_key: 'medication:new-med',
          entity_id: null,
          action: 'create',
          content_hash: 'h2',
          update_available: false,
          conflict: null,
          warnings: [],
        },
      ],
    },
    retire: [],
    warnings: ['homoglyph warning'],
    errors: [],
    summary: { create: 1, update: 0, noop: 0, skip: 1, conflict: 0, retire: 0, warnings: 1, errors: 0 },
  };
}

/** Fire the confirmation the page parked in the ui store (the global
 * ConfirmationModal is the one that would call onConfirm in the app). */
async function confirmPendingDialog(): Promise<void> {
  const modal = useUIStore.getState().confirmationModal;
  expect(modal).not.toBeNull();
  await modal?.onConfirm();
  useUIStore.getState().hideConfirmation();
}

beforeEach(() => {
  vi.clearAllMocks();
  useUIStore.setState({ confirmationModal: null });
  useAccessStore.setState({
    permissions: new Set(['seed_source:read', 'seed_source:manage', 'seed_source:approve']),
    roles: ['SYSTEM_ADMIN'],
    tenant: { id: 't1', name: 'Clinic' },
    patientGrants: [],
    pendingGrants: [],
    isHydrated: true,
    hasError: false,
  });
  mocked.listSeedSources.mockResolvedValue([sourceRow()]);
});

describe('SeedSources', () => {
  it('renders the sources table with kind, version, digest, priority and scope', async () => {
    render(<SeedSources scope="system" />);
    expect(await screen.findByText(/acme-catalog/)).toBeInTheDocument();
    expect(screen.getByText(/1\.2\.0/)).toBeInTheDocument();
    expect(screen.getByText('aaaaaaaaaa…')).toBeInTheDocument();
    expect(screen.getByText('system')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
    expect(screen.getByText('https://github.com/org/catalog-packs.git')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Download seed pack' }),
    ).toBeInTheDocument();
  });

  it('gates the seed pack download to system admins (the export route is SYSTEM-scoped)', async () => {
    useAccessStore.setState({
      roles: ['ADMIN'],
      permissions: new Set(['seed_source:read', 'seed_source:manage', 'seed_source:approve']),
    });
    render(<SeedSources scope="tenant" />);
    expect(await screen.findByText('https://github.com/org/catalog-packs.git')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Download seed pack' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add source' })).toBeInTheDocument();
  });

  it('hides the seed pack download without system admin even on the system scope', async () => {
    useAccessStore.setState({
      roles: ['ADMIN'],
      permissions: new Set(['seed_source:read']),
    });
    render(<SeedSources scope="system" />);
    expect(await screen.findByText('https://github.com/org/catalog-packs.git')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Download seed pack' })).not.toBeInTheDocument();
  });

  it('surfaces last_error on the row', async () => {
    mocked.listSeedSources.mockResolvedValue([
      sourceRow({ last_error: 'DIGEST_DRIFT: upstream moved' }),
    ]);
    render(<SeedSources scope="system" />);
    expect(await screen.findByText(/DIGEST_DRIFT/)).toBeInTheDocument();
  });

  it('shows the empty state when no sources exist', async () => {
    mocked.listSeedSources.mockResolvedValue([]);
    render(<SeedSources scope="tenant" />);
    expect(await screen.findByText('No tenant overlay sources yet.')).toBeInTheDocument();
  });

  it('syncs a source from the row action', async () => {
    const user = userEvent.setup();
    mocked.syncSeedSource.mockResolvedValue({ status: 'registered' });
    render(<SeedSources scope="system" />);
    await user.click(await screen.findByRole('button', { name: 'Sync now' }));
    await waitFor(() => expect(mocked.syncSeedSource).toHaveBeenCalledWith('src-1'));
    await waitFor(() => expect(mocked.listSeedSources).toHaveBeenCalledTimes(2));
  });

  it('opens the plan preview for a pending version and approves it', async () => {
    const user = userEvent.setup();
    mocked.planSeedVersion.mockResolvedValue(planDoc());
    mocked.approveSeedVersion.mockResolvedValue({} as never);
    render(<SeedSources scope="system" />);
    await user.click(await screen.findByRole('button', { name: 'Review' }));
    expect(await screen.findByText('medication:aspirin')).toBeInTheDocument();
    expect(
      screen.getByText('Update available — your edit is kept until you adopt'),
    ).toBeInTheDocument();
    expect(screen.getByText('homoglyph warning')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(mocked.approveSeedVersion).toHaveBeenCalledWith('ver-1'));
  });

  it('reviews and approves the new pending version while an approved one is current', async () => {
    const user = userEvent.setup();
    const approved = versionRow('ver-approved', {
      status: 'approved',
      is_current: true,
      created_at: '2026-11-08T10:00:00Z',
      approved_by: 'admin-1',
      approved_at: '2026-11-08T11:00:00Z',
      latest_run: {
        run_id: 'run-1',
        mode: 'apply',
        status: 'ok',
        started_at: '2026-11-08T11:05:00Z',
        stats: { create: 3, update: 0, noop: 9, skip: 0, conflict: 0, retire: 0 },
      },
    });
    const pending = versionRow('ver-pending', {
      digest: 'c'.repeat(64),
      pack_version: '1.3.0',
      created_at: '2026-11-09T12:00:00Z',
    });
    mocked.listSeedSources.mockResolvedValue([
      sourceRow({ current_version_id: 'ver-approved', versions: [pending, approved] }),
    ]);
    mocked.planSeedVersion.mockResolvedValue(planDoc());
    mocked.approveSeedVersion.mockResolvedValue({} as never);

    render(<SeedSources scope="system" />);
    const review = await screen.findByRole('button', { name: 'Review' });
    expect(review).toBeInTheDocument();
    await user.click(review);
    expect(await screen.findByText('medication:aspirin')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(mocked.approveSeedVersion).toHaveBeenCalledWith('ver-pending'));
    expect(mocked.approveSeedVersion).not.toHaveBeenCalledWith('ver-approved');
  });

  it('keeps Plan / apply and Revoke on the applied version while a pending one waits', async () => {
    const user = userEvent.setup();
    const approved = versionRow('ver-approved', {
      status: 'approved',
      is_current: true,
      created_at: '2026-11-08T10:00:00Z',
    });
    const pending = versionRow('ver-pending', {
      digest: 'c'.repeat(64),
      pack_version: '1.3.0',
      created_at: '2026-11-09T12:00:00Z',
    });
    mocked.listSeedSources.mockResolvedValue([
      sourceRow({ current_version_id: 'ver-approved', versions: [pending, approved] }),
    ]);
    mocked.planSeedVersion.mockResolvedValue(planDoc());

    render(<SeedSources scope="system" />);
    await screen.findByRole('button', { name: 'Review' });
    const planApply = screen.getByRole('button', { name: 'Plan / apply' });
    await user.click(planApply);
    expect(await screen.findByText('medication:aspirin')).toBeInTheDocument();
    // The applied version is what the modal planned, not the pending one.
    expect(mocked.planSeedVersion).toHaveBeenCalledWith('ver-approved');
  });

  it('adopts update-available rows from the plan preview', async () => {
    const user = userEvent.setup();
    mocked.planSeedVersion.mockResolvedValue(planDoc());
    mocked.adoptSeedUpdates.mockResolvedValue({ cleared: 1 });
    render(<SeedSources scope="system" />);
    await user.click(await screen.findByRole('button', { name: 'Review' }));
    await user.click(await screen.findByRole('button', { name: 'Adopt updates' }));
    await waitFor(() => expect(mocked.adoptSeedUpdates).toHaveBeenCalledWith('src-1'));
  });

  it('revokes the current version through the confirm dialog', async () => {
    const user = userEvent.setup();
    const row = sourceRow();
    row.versions[0].status = 'approved';
    row.versions[0].is_current = true;
    row.versions[0].latest_run = {
      run_id: 'run-1',
      mode: 'apply',
      status: 'ok',
      started_at: '2026-11-09T10:05:00Z',
      stats: { create: 3, update: 0, noop: 9, skip: 0, conflict: 0, retire: 0 },
    };
    mocked.listSeedSources.mockResolvedValue([row]);
    mocked.revokeSeedVersion.mockResolvedValue({});

    render(<SeedSources scope="system" />);
    await user.click(await screen.findByRole('button', { name: 'Revoke' }));
    await confirmPendingDialog();
    await waitFor(() => expect(mocked.revokeSeedVersion).toHaveBeenCalledWith('ver-1'));
  });

  it('retires a source through the confirm dialog', async () => {
    const user = userEvent.setup();
    mocked.retireSeedSource.mockResolvedValue({ retired: 2, kept: 1 });
    render(<SeedSources scope="system" />);
    await user.click(await screen.findByRole('button', { name: 'Retire' }));
    await confirmPendingDialog();
    await waitFor(() => expect(mocked.retireSeedSource).toHaveBeenCalledWith('src-1'));
  });

  it('hides management actions without seed_source:manage', async () => {
    useAccessStore.setState({ permissions: new Set(['seed_source:read']) });
    render(<SeedSources scope="system" />);
    expect(await screen.findByText('https://github.com/org/catalog-packs.git')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sync now' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add source' })).not.toBeInTheDocument();
  });
});
