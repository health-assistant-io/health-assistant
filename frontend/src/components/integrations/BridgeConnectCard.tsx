import { useEffect, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { Smartphone, Copy, Check, KeyRound, Loader2, AlertTriangle } from 'lucide-react';
import api from '../../api/axios';

/**
 * Shown on a Health Assistant Bridge integration's detail page so a user can
 * onboard the Android app without typing by hand. The base URL in the QR
 * resolves as: per-instance `connect_url` (config flow) → the server's
 * `client_base_url` SystemSetting (exposed at `/config/public`) → the URL the
 * admin is browsing from.
 *
 * Machine secrets are MANDATORY (2026-08 audit): the app must present an
 * HMAC signature on every data route. Because the stored plaintext can never
 * be re-displayed (Fernet-encrypted, row-bound), the pairing flow is
 * secret-at-a-time: "Show pairing code" ROTATES the instance's api_secret and
 * renders a QR/connection code carrying it (`base|instance|secret`) — the
 * app's Onboarding.parseQr accepts exactly this three-segment form. The old
 * secret dies the moment a new code is generated.
 */
export function BridgeConnectCard({
  instanceId,
  patientId,
  hasApiSecret,
  connectUrl,
}: {
  instanceId: string;
  patientId?: string;
  hasApiSecret: boolean;
  connectUrl?: string;
}) {
  const [serverClientBaseUrl, setServerClientBaseUrl] = useState('');
  const [rotating, setRotating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pairingSecret, setPairingSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const activePatientId = patientId || new URLSearchParams(window.location.search).get('patient_id') || '';
  const effectiveHasSecret = hasApiSecret || Boolean(pairingSecret);

  useEffect(() => {
    api
      .get('/config/public')
      .then(({ data }) => setServerClientBaseUrl(data?.client_base_url || ''))
      .catch(() => setServerClientBaseUrl(''));
  }, []);

  const baseUrl = (connectUrl || serverClientBaseUrl || window.location.origin).replace(/\/$/, '');
  const code = pairingSecret
    ? `${baseUrl}|${instanceId}|${pairingSecret}`
    : `${baseUrl}|${instanceId}`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable */
    }
  };

  const generatePairingCode = async () => {
    if (!activePatientId) {
      setError('Missing patient context — open this card from the integration detail page.');
      return;
    }
    setRotating(true);
    setError(null);
    try {
      const { data } = await api.post(
        `/integrations/instance/${instanceId}/rotate-secret`,
        null,
        { params: { patient_id: activePatientId, field: 'api_secret' } },
      );
      setPairingSecret(data.api_secret as string);
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to generate a pairing code.');
    } finally {
      setRotating(false);
    }
  };

  return (
    <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
      <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-text mb-2">
        <Smartphone className="w-5 h-5 mr-2 text-blue-500" /> Connect your mobile app
      </h3>
      <p className="text-sm text-gray-600 dark:text-dark-muted mb-6">
        Scan this with the Health Assistant Android app, or copy the connection code into it.
      </p>

      {!pairingSecret ? (
        <div className="flex flex-col gap-3">
          <button
            onClick={generatePairingCode}
            disabled={rotating}
            className="inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-60 transition-colors"
          >
            {rotating ? <Loader2 className="w-4 h-4 animate-spin" /> : <KeyRound className="w-4 h-4" />}
            {effectiveHasSecret ? 'Regenerate pairing code' : 'Show pairing code'}
          </button>
          <p className="text-xs text-gray-500 dark:text-dark-muted">
            {effectiveHasSecret
              ? 'This instance has an API secret. Generating a new pairing code replaces it — devices paired with the old secret must re-pair.'
              : 'The app requires a signing secret (it is included in the code automatically). Generating one takes effect immediately.'}
          </p>
        </div>
      ) : (
        <>
          <div className="flex flex-col sm:flex-row items-center gap-6">
            <div className="bg-white p-3 rounded-2xl border border-gray-200 shrink-0">
              <QRCodeSVG value={code} size={148} level="M" />
            </div>
            <div className="flex-1 w-full min-w-0">
              <label className="block text-xs font-semibold text-gray-500 dark:text-dark-muted mb-1 uppercase tracking-wide">
                Connection code (includes the pairing secret)
              </label>
              <div className="flex items-center gap-2">
                <code className="flex-1 min-w-0 text-xs bg-gray-100 dark:bg-dark-bg rounded-lg px-3 py-2 break-all">
                  {code}
                </code>
                <button
                  onClick={copy}
                  title="Copy connection code"
                  className="shrink-0 inline-flex items-center justify-center w-9 h-9 rounded-lg border border-gray-300 text-gray-600 bg-white hover:bg-gray-50 dark:bg-dark-bg dark:text-dark-text dark:border-dark-border dark:hover:bg-dark-surface transition-colors"
                >
                  {copied ? <Check className="w-4 h-4 text-emerald-600" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
              <p className="mt-3 flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 rounded-lg px-3 py-2">
                <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>
                  Shown once — the secret is not stored in visible form and any previously paired code
                  stopped working when this one was generated.
                </span>
              </p>
            </div>
          </div>
          <button
            onClick={generatePairingCode}
            disabled={rotating}
            className="mt-4 inline-flex items-center gap-2 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-60"
          >
            {rotating && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Generate a new code (invalidates this one)
          </button>
        </>
      )}

      {error && (
        <p className="mt-3 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2">
          {error}
        </p>
      )}
    </div>
  );
}
