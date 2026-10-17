import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import Integrations from './Integrations';
import { integrationService } from '../../services/integrationService';
import type { ActiveIntegration, IntegrationManifest } from '../../services/integrationService';

vi.mock('../../services/integrationService', () => ({
  integrationService: {
    getAvailable: vi.fn(),
    getActive: vi.fn(),
    removeIntegration: vi.fn(),
    getConfigFlow: vi.fn(),
    submitConfigFlow: vi.fn(),
  },
}));

vi.mock('../../store/slices/patientSlice', () => ({
  usePatientStore: () => ({ currentPatient: { id: 'pat-1' } }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));

const available: IntegrationManifest[] = [
  {
    domain: 'webhook',
    name: 'Webhook Receiver',
    version: '1.2.0',
    integration_type: ['pull', 'push'],
    description: 'Receives webhook events.',
    author: 'Core',
    access_type: 'cloud',
    categories: ['data', 'productivity'],
    kind: 'connector',
    capabilities: ['net'],
  },
  {
    domain: 'fhir_server',
    name: 'FHIR Server',
    version: '2.0.0',
    integration_type: ['pull'],
    description: 'Hospital FHIR.',
    author: 'Core',
    access_type: 'local',
    categories: ['data'],
    kind: 'connector',
    capabilities: ['net', 'fs-read'],
  },
];

const active: ActiveIntegration[] = [
  {
    id: 'inst-1',
    domain: 'webhook',
    instance_name: 'Garage door',
    status: 'ACTIVE',
    last_synced_at: '2026-10-01T10:00:00Z',
    sync_status: { status: 'ok', last_success_at: '2026-10-10T10:00:00Z', consecutive_failures: 0 },
  },
  {
    id: 'inst-2',
    domain: 'webhook',
    instance_name: 'Kitchen scale',
    status: 'ERROR',
    last_synced_at: null,
  },
  {
    id: 'inst-3',
    domain: 'fhir_server',
    instance_name: null,
    status: 'ACTIVE',
    last_synced_at: null,
    sync_status: { status: 'ok', consecutive_failures: 0, conditions: ['needs-config'] },
  },
];

function setup() {
  vi.mocked(integrationService.getAvailable).mockResolvedValue(available);
  vi.mocked(integrationService.getActive).mockResolvedValue(active);
  return render(
    <MemoryRouter>
      <Integrations />
    </MemoryRouter>,
  );
}

describe('Integrations page (kit instances list + catalog)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('confirm', vi.fn(() => true));
  });

  it('renders instances grouped under their extension with family status labels', async () => {
    setup();
    expect(await screen.findByTestId('integration-group-webhook')).toBeTruthy();
    expect(screen.getByTestId('integration-instance-inst-1').textContent).toContain('Garage door');
    expect(screen.getByTestId('integration-instance-inst-2').textContent).toContain('Kitchen scale');
    expect(screen.getAllByText('Connected').length).toBe(2);
    expect(screen.getByText('Error — requires attention')).toBeTruthy();
    expect(screen.getByTestId('integration-group-fhir_server').textContent).toContain('needs config');
  });

  it('removes through the kit tombstone action after the app confirm', async () => {
    const user = userEvent.setup();
    vi.mocked(integrationService.removeIntegration).mockResolvedValue(undefined);
    setup();
    await screen.findByTestId('integration-group-webhook');
    await user.click(screen.getByLabelText('Remove: Kitchen scale'));
    await waitFor(() =>
      expect(integrationService.removeIntegration).toHaveBeenCalledWith('inst-2', 'pat-1'),
    );
  });

  it('links to the instance detail through the instance-actions slot', async () => {
    setup();
    await screen.findByTestId('integration-group-webhook');
    const link = screen.getAllByRole('link', { name: /Details/ })[0];
    expect(link.getAttribute('href')).toBe('/settings/integrations/inst-1');
  });

  it('hosts the browse catalog in the kit grid and hands Add to the config flow', async () => {
    const user = userEvent.setup();
    vi.mocked(integrationService.getConfigFlow).mockResolvedValue({
      step_id: 'config',
      title: 'Configure Webhook Receiver',
      data_schema: { type: 'object', properties: { name: { type: 'string', title: 'Name' } } },
    });
    setup();
    await screen.findByTestId('integration-group-webhook');
    await user.click(screen.getByText('Browse & Connect'));
    expect((await screen.findAllByTestId('integration-catalog-entry-webhook')).length).toBe(2);
    expect(screen.getAllByTestId('integration-catalog-entry-fhir_server').length).toBeGreaterThan(0);
    await user.click(screen.getAllByText('Add')[0]);
    expect(await screen.findByTestId('integration-config-form')).toBeTruthy();
    expect(screen.getByText('Configure Webhook Receiver')).toBeTruthy();
  });
});
