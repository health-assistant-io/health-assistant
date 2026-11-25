import { useCallback, useEffect, useMemo, useState } from 'react';
import { Coins, Plus, Trash2 } from 'lucide-react';
import {
  deleteAIPrice,
  getAICosts,
  getAIPrices,
  saveAIPrice,
  type AICostsResponse,
  type AIPricesResponse,
} from '../../api/aiConfig';
import { Button } from '../ui/Button';

import { formatNumber } from '../../lib/dateFormats';
import { useTranslation } from 'react-i18next';
function usd(micros: number | null): string {
  if (micros === null) return '—';
  return `$${(micros / 1_000_000).toFixed(4)}`;
}

function perMillion(micros: number): string {
  return `$${(micros / 1_000_000).toFixed(2)}`;
}

const WINDOWS = [7, 30, 90] as const;

export function DataCostTab({ tenantId }: { tenantId?: string }) {
  const { t } = useTranslation();
  const [costs, setCosts] = useState<AICostsResponse | null>(null);
  const [prices, setPrices] = useState<AIPricesResponse | null>(null);
  const [windowDays, setWindowDays] = useState<number>(30);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    model: '',
    provider_key: '',
    input: '',
    output: '',
    match: 'exact' as 'exact' | 'prefix',
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [costData, priceData] = await Promise.all([
        getAICosts(windowDays, tenantId),
        getAIPrices(tenantId),
      ]);
      setCosts(costData);
      setPrices(priceData);
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? t('settings.cost_load_failed'));
    } finally {
      setLoading(false);
    }
  }, [tenantId, windowDays, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const totals = useMemo(() => {
    const rows = costs?.rows ?? [];
    return {
      cost: rows.reduce((sum, row) => sum + (row.cost_micros ?? 0), 0),
      priced: rows.reduce((sum, row) => sum + row.priced_calls, 0),
      unpriced: rows.reduce((sum, row) => sum + row.unpriced_calls, 0),
    };
  }, [costs]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      await saveAIPrice({
        tenant_id: tenantId,
        model: form.model.trim(),
        provider_key: form.provider_key.trim() || null,
        input_micros_per_million: Math.round(Number(form.input || 0) * 1_000_000),
        output_micros_per_million: Math.round(Number(form.output || 0) * 1_000_000),
        match: form.match,
        metering: 'tokens',
      });
      setShowForm(false);
      setForm({ model: '', provider_key: '', input: '', output: '', match: 'exact' });
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? t('settings.cost_save_failed'));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: string) => {
    setError('');
    try {
      await deleteAIPrice(id);
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? t('settings.cost_delete_failed'));
    }
  };

  if (loading) {
    return <div className="py-10 text-center text-sm text-gray-500">{t('settings.cost_loading')}</div>;
  }

  return (
    <div className="space-y-6" data-testid="ai-data-cost">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-gray-900 dark:text-white">
            <Coins className="h-5 w-5" />{t('settings.cost_title')}</h2>
          <p className="mt-1 max-w-2xl text-sm text-gray-500">
            {t('settings.cost_intro')}
          </p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold tabular-nums text-gray-900 dark:text-white">
            {usd(totals.cost)}
          </div>
          <div className="text-xs text-gray-500">
            {totals.priced} priced · {totals.unpriced} unpriced
          </div>
        </div>
      </div>

      {error && <p className="text-sm text-rose-600">{error}</p>}

      <div className="flex items-center gap-2">
        {WINDOWS.map((days) => (
          <button
            key={days}
            type="button"
            onClick={() => setWindowDays(days)}
            className={`rounded-md px-3 py-1.5 text-sm ${
              windowDays === days
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-200'
            }`}
          >
            {days} days
          </button>
        ))}
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500 dark:bg-gray-800">
            <tr>
              <th className="px-3 py-2">{t('settings.cost_provider')}</th>
              <th className="px-3 py-2">{t('settings.cost_model')}</th>
              <th className="px-3 py-2 text-right">{t('settings.cost_calls')}</th>
              <th className="px-3 py-2 text-right">{t('settings.cost_tokens')}</th>
              <th className="px-3 py-2 text-right">{t('settings.cost_cost')}</th>
            </tr>
          </thead>
          <tbody>
            {(costs?.rows ?? []).length === 0 ? (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-gray-500">{t('settings.cost_empty')}</td>
              </tr>
            ) : (
              costs!.rows.map((row) => (
                <tr
                  key={`${row.provider}:${row.model}`}
                  className="border-t border-gray-100 dark:border-gray-800"
                >
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-300">{row.provider}</td>
                  <td className="px-3 py-2 text-gray-900 dark:text-white">
                    {row.model}
                    {row.unpriced_calls > 0 && (
                      <span className="ml-2 text-xs text-amber-600">
                        {row.unpriced_calls} unpriced
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{row.calls}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {formatNumber(row.prompt_tokens + row.completion_tokens)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{usd(row.cost_micros)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-white">{t('settings.cost_overrides')}</h3>
          <Button size="sm" variant="outline" onClick={() => setShowForm((v) => !v)}>
            <Plus className="mr-1 h-4 w-4" />{t('settings.cost_add_override')}</Button>
        </div>

        {showForm && (
          <form
            onSubmit={submit}
            className="grid gap-3 rounded-lg border border-gray-200 p-4 md:grid-cols-5 dark:border-gray-700"
          >
            <label className="text-xs text-gray-600 md:col-span-2 dark:text-gray-300">{t('settings.cost_model_id')}<input
                required
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
                className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
                placeholder="gpt-4o"
              />
            </label>
            <label className="text-xs text-gray-600 dark:text-gray-300">{t('settings.cost_preset_key')}<input
                value={form.provider_key}
                onChange={(e) => setForm({ ...form, provider_key: e.target.value })}
                className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
                placeholder="openai"
              />
            </label>
            <label className="text-xs text-gray-600 dark:text-gray-300">{t('settings.cost_input_price')}<input
                required
                type="number"
                min="0"
                step="0.01"
                value={form.input}
                onChange={(e) => setForm({ ...form, input: e.target.value })}
                className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
              />
            </label>
            <label className="text-xs text-gray-600 dark:text-gray-300">{t('settings.cost_output_price')}<input
                required
                type="number"
                min="0"
                step="0.01"
                value={form.output}
                onChange={(e) => setForm({ ...form, output: e.target.value })}
                className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-900"
              />
            </label>
            <div className="flex items-end md:col-span-5">
              <Button type="submit" size="sm" disabled={saving}>
                {saving ? t('common.saving') : t('settings.cost_save')}
              </Button>
            </div>
          </form>
        )}

        <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500 dark:bg-gray-800">
              <tr>
                <th className="px-3 py-2">{t('settings.cost_model')}</th>
                <th className="px-3 py-2">{t('settings.cost_binding')}</th>
                <th className="px-3 py-2 text-right">{t('settings.cost_input')}</th>
                <th className="px-3 py-2 text-right">{t('settings.cost_output')}</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {(prices?.overrides ?? []).length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-gray-500">{t('settings.cost_no_overrides')}</td>
                </tr>
              ) : (
                prices!.overrides.map((row) => (
                  <tr
                    key={row.id}
                    className="border-t border-gray-100 dark:border-gray-800"
                  >
                    <td className="px-3 py-2 text-gray-900 dark:text-white">{row.model}</td>
                    <td className="px-3 py-2 text-gray-500">
                      {row.provider_key ?? row.provider_ref ?? 'tenant default'}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {perMillion(row.input_micros_per_million)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {perMillion(row.output_micros_per_million)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        type="button"
                        aria-label={`Delete price override for ${row.model}`}
                        onClick={() => void remove(row.id)}
                        className="text-gray-400 hover:text-rose-600"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
