import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => {
  const t = (key: string, fallbackOrOptions?: unknown, options?: unknown) => {
    const text =
      typeof fallbackOrOptions === 'string' ? fallbackOrOptions : key;
    const vars =
      options ??
      (fallbackOrOptions && typeof fallbackOrOptions === 'object'
        ? fallbackOrOptions
        : {});
    return text.replace(/\{\{(\w+)\}\}/g, (_match, name: string) =>
      String((vars as Record<string, unknown>)[name] ?? ''),
    );
  };
  return { useTranslation: () => ({ t, i18n: { language: 'en' } }) };
});

const mocks = vi.hoisted(() => ({ fetchStorageStatus: vi.fn() }));

vi.mock('../../../services/storageService', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../../services/storageService')>();
  return {
    ...actual,
    fetchStorageStatus: (refresh?: boolean) =>
      mocks.fetchStorageStatus(refresh),
  };
});

import StorageStatusPage from '../StorageStatus';

function status(overrides: Record<string, unknown> = {}) {
  return {
    backend: 's3',
    entrypoint: 'server',
    cloud_profile: 'aws',
    bucket: 'health-assistant-prod',
    container: null,
    region: 'eu-north-1',
    endpoint_host: 's3.eu-north-1.amazonaws.com',
    prefix: 'prod',
    sse: 'AES256',
    max_object_mb: 1024,
    presign_ttl_seconds: 300,
    required: true,
    status: 'ok',
    healthy: true,
    detail: null,
    checked_at: '2026-11-15T12:00:00Z',
    ...overrides,
  };
}

describe('StorageStatusPage', () => {
  beforeEach(() => {
    mocks.fetchStorageStatus.mockReset();
  });

  it('renders the resolved target as read-only facts', async () => {
    mocks.fetchStorageStatus.mockResolvedValue(status());
    render(<StorageStatusPage />);

    expect(await screen.findByText('health-assistant-prod')).toBeInTheDocument();
    expect(screen.getAllByText('Storage status').length).toBeGreaterThan(0);
    expect(screen.getByText('prod')).toBeInTheDocument();
    expect(
      screen.getByText('eu-north-1 · s3.eu-north-1.amazonaws.com'),
    ).toBeInTheDocument();
    expect(screen.getByText('AES256')).toBeInTheDocument();
    expect(screen.getByText('1024 MB')).toBeInTheDocument();
    expect(screen.getByText('300 s')).toBeInTheDocument();
    expect(screen.getByText('Healthy')).toBeInTheDocument();
    expect(screen.queryByText(/X-Amz-/)).not.toBeInTheDocument();
    expect(screen.queryByText(/access key/i)).not.toBeInTheDocument();
  });

  it('shows a loading state before the first snapshot', async () => {
    let resolve: (value: ReturnType<typeof status>) => void = () => {};
    mocks.fetchStorageStatus.mockReturnValue(
      new Promise<ReturnType<typeof status>>((done) => {
        resolve = done;
      }),
    );
    render(<StorageStatusPage />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Reading deployment storage status…',
    );
    resolve(status());
    expect(await screen.findByText('health-assistant-prod')).toBeInTheDocument();
  });

  it('the test button forces a fresh probe and renders the new health row', async () => {
    mocks.fetchStorageStatus
      .mockResolvedValueOnce(status())
      .mockResolvedValueOnce(
        status({
          status: 'unavailable',
          healthy: false,
          detail: 'StorageUnavailable',
          checked_at: '2026-11-15T12:00:30Z',
        }),
      );
    render(<StorageStatusPage />);
    expect(await screen.findByText('Healthy')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));

    expect(await screen.findByText('Unavailable')).toBeInTheDocument();
    await waitFor(() =>
      expect(mocks.fetchStorageStatus).toHaveBeenLastCalledWith(true),
    );
  });

  it('shows read and mutation errors', async () => {
    mocks.fetchStorageStatus.mockRejectedValueOnce(
      new Error('storage status unavailable'),
    );
    const first = render(<StorageStatusPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'storage status unavailable',
    );
    first.unmount();

    mocks.fetchStorageStatus
      .mockResolvedValueOnce(status())
      .mockRejectedValueOnce(new Error('probe failed'));
    render(<StorageStatusPage />);
    expect(await screen.findByText('Healthy')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('probe failed');
  });
});
