import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConfigFlowModal from './ConfigFlowModal';
import { integrationService } from '../../services/integrationService';
import type { ConfigFlowSchema } from '../../services/integrationService';

const submitConfigFlow = vi.fn();

vi.mock('../../services/integrationService', () => ({
  integrationService: {
    getConfigFlow: vi.fn(),
    submitConfigFlow: (...args: unknown[]) => submitConfigFlow(...args),
  },
}));

vi.mock('../../store/slices/patientSlice', () => ({
  usePatientStore: () => ({ currentPatient: { id: 'pat-1' } }),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));

const schema: ConfigFlowSchema = {
  step_id: 'config',
  title: 'Configure MCP Client',
  description: 'Connect an MCP server.',
  data_schema: {
    type: 'object',
    required: ['name'],
    properties: {
      name: { type: 'string', title: 'Instance name' },
      transport: { type: 'string', title: 'Transport', enum: ['stdio', 'http'], enum_descriptions: { stdio: 'Local process', http: 'Remote URL' } },
      api_key: { type: 'string', title: 'API key', 'x-secret': true },
      command: { type: 'string', title: 'Command', depends_on: { field: 'transport', value: 'stdio' } },
      headers: { type: 'object', title: 'Headers', 'x-format': 'key-value' },
      record_types: { type: 'array', title: 'Record types', items: { type: 'string', enum: ['observations', 'medications'] } },
      options_json: { type: 'string', title: 'Options', format: 'json' },
    },
  },
};

function setup(initialData: Record<string, unknown> = {}) {
  vi.mocked(integrationService.getConfigFlow).mockResolvedValue(schema);
  return render(
    <ConfigFlowModal domain="mcp_client" onClose={vi.fn()} onSuccess={vi.fn()} initialData={initialData} />,
  );
}

describe('ConfigFlowModal (kit form host)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the kit config form with a sealed secret slot for masked stored secrets', async () => {
    setup({ api_key: '***' });
    expect(await screen.findByTestId('integration-config-form')).toBeTruthy();
    expect(screen.getByText('Instance name')).toBeTruthy();
    const secret = screen.getByLabelText(/^API key/) as HTMLInputElement;
    expect(secret.type).toBe('password');
    expect(screen.getByTestId('integration-config-secret-api_key').textContent).toContain(
      'set — leave empty to keep',
    );
  });

  it("keeps health's extended dialect app-side: kv rows, constrained toggles and the JSON textarea render outside the kit form", async () => {
    setup();
    expect(await screen.findByTestId('integration-config-form')).toBeTruthy();
    expect(screen.getByText('Headers')).toBeTruthy();
    expect(screen.getByText('Record types')).toBeTruthy();
    expect(screen.getByText('Options')).toBeTruthy();
  });

  it("shows the selected enum option's description through the field view", async () => {
    setup();
    await screen.findByTestId('integration-config-form');
    expect(screen.getByText('Local process')).toBeTruthy();
  });

  it('submits kit values + app-dialect values with untouched secrets sent empty', async () => {
    const user = userEvent.setup();
    submitConfigFlow.mockResolvedValue({ message: 'ok' });
    setup({ api_key: '***', headers: { 'X-Trace': 'abc' } });
    await screen.findByTestId('integration-config-form');

    await user.type(screen.getByLabelText(/^Instance name/), 'Garage');
    await user.type(screen.getByLabelText(/^API key/), 'tok-1');
    await user.click(screen.getByText('Save Configuration'));

    await waitFor(() => expect(submitConfigFlow).toHaveBeenCalledTimes(1));
    const [, , payload, integrationId] = submitConfigFlow.mock.calls[0];
    expect(integrationId).toBeUndefined();
    expect(payload).toMatchObject({
      name: 'Garage',
      transport: '',
      api_key: 'tok-1',
      headers: { 'X-Trace': 'abc' },
      record_types: [],
    });
  });

  it("routes the endpoint's 400 detail through the kit validation display", async () => {
    const user = userEvent.setup();
    submitConfigFlow.mockRejectedValue({ response: { data: { detail: 'name is required' } } });
    setup();
    await screen.findByTestId('integration-config-form');
    await user.click(screen.getByText('Save Configuration'));
    await waitFor(() =>
      expect(screen.getByTestId('integration-config-validation-error').textContent).toContain(
        'name is required',
      ),
    );
  });
});
