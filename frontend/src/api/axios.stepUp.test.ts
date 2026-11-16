import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AxiosError } from 'axios';
import api from './axios';
import {
  registerStepUpPresenter,
  resetStepUpController,
} from '../components/auth/stepUpController';

vi.mock('../services/offlineService', () => ({
  offlineService: { addToQueue: vi.fn() },
}));

const ACTION = 'export';

function stepUpError(config: unknown) {
  return new AxiosError(
    'Request failed with status code 403',
    AxiosError.ERR_BAD_REQUEST,
    config as never,
    {},
    {
      data: { detail: { code: 'step_up_required', action: ACTION, expires_in: 600 } },
      status: 403,
      statusText: 'Forbidden',
      headers: {},
      config: config as never,
    },
  );
}

let attempts = 0;
let alwaysDeny = false;

api.defaults.adapter = async (config) => {
  attempts += 1;
  if (alwaysDeny || attempts === 1) {
    throw stepUpError(config);
  }
  return {
    data: { ok: true },
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  };
};

describe('axios interceptor — step-up retry (program 12C1)', () => {
  beforeEach(() => {
    attempts = 0;
    alwaysDeny = false;
    resetStepUpController();
  });

  afterEach(() => {
    resetStepUpController();
  });

  it('prompts once, then replays the original request', async () => {
    const presenter = vi.fn(async () => true);
    registerStepUpPresenter(presenter);

    const response = await api.post('/export', { format: 'fhir' });

    expect(response.data).toEqual({ ok: true });
    expect(attempts).toBe(2);
    expect(presenter).toHaveBeenCalledTimes(1);
    expect(presenter).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'step_up_required', action: ACTION }),
    );
  });

  it('rejects the original error when the user cancels', async () => {
    const presenter = vi.fn(async () => false);
    registerStepUpPresenter(presenter);

    await expect(api.post('/export')).rejects.toMatchObject({ response: { status: 403 } });
    expect(attempts).toBe(1);
    expect(presenter).toHaveBeenCalledTimes(1);
  });

  it('never loops: a replay that is denied again rejects without a second prompt', async () => {
    alwaysDeny = true;
    const presenter = vi.fn(async () => true);
    registerStepUpPresenter(presenter);

    await expect(api.post('/export')).rejects.toMatchObject({ response: { status: 403 } });
    expect(attempts).toBe(2);
    expect(presenter).toHaveBeenCalledTimes(1);
  });

  it('proceeds without a prompt when no UI is mounted (fail closed, no loop)', async () => {
    await expect(api.post('/export')).rejects.toMatchObject({ response: { status: 403 } });
    expect(attempts).toBe(1);
  });
});
