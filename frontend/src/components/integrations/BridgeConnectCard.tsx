import { useEffect, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { Smartphone, Copy, Check } from 'lucide-react';
import api from '../../api/axios';

/**
 * Shown on a Health Assistant Bridge integration's detail page so a user can
 * onboard the Android app without typing a UUID/secret by hand. The base URL in
 * the QR resolves as: per-instance `connect_url` (config flow) → the server's
 * `client_base_url` SystemSetting (exposed at `/config/public`) → the URL the
 * admin is browsing from. The QR encodes the `base_url|integration_id` code the
 * app's Onboarding.parseQr accepts (UUID-only mode). When an API secret is
 * configured, it is encrypted at rest and masked server-side, so it cannot be
 * embedded here — the user enters it once in the app's manual section.
 */
export function BridgeConnectCard({
  instanceId,
  hasApiSecret,
  connectUrl,
}: {
  instanceId: string;
  hasApiSecret: boolean;
  connectUrl?: string;
}) {
  const [serverClientBaseUrl, setServerClientBaseUrl] = useState('');
  useEffect(() => {
    api
      .get('/config/public')
      .then(({ data }) => setServerClientBaseUrl(data?.client_base_url || ''))
      .catch(() => setServerClientBaseUrl(''));
  }, []);

  const baseUrl = (connectUrl || serverClientBaseUrl || window.location.origin).replace(/\/$/, '');
  const code = `${baseUrl}|${instanceId}`;
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable */
    }
  };

  return (
    <div className="bg-white dark:bg-dark-surface rounded-[2rem] p-8 border border-gray-100 dark:border-dark-border shadow-sm">
      <h3 className="flex items-center text-lg font-bold text-gray-900 dark:text-dark-text mb-2">
        <Smartphone className="w-5 h-5 mr-2 text-blue-500" /> Connect your mobile app
      </h3>
      <p className="text-sm text-gray-600 dark:text-dark-muted mb-6">
        Scan this with the Health Assistant Android app, or copy the connection code into it.
      </p>
      <div className="flex flex-col sm:flex-row items-center gap-6">
        <div className="bg-white p-3 rounded-2xl border border-gray-200 shrink-0">
          <QRCodeSVG value={code} size={148} level="M" />
        </div>
        <div className="flex-1 w-full min-w-0">
          <label className="block text-xs font-semibold text-gray-500 dark:text-dark-muted mb-1 uppercase tracking-wide">
            Connection code
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
          {hasApiSecret ? (
            <p className="mt-3 text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 rounded-lg px-3 py-2">
              This instance has an API secret. After scanning, open <span className="font-semibold">“Enter details manually”</span> in the app and paste the secret — it is encrypted at rest and never displayed here.
            </p>
          ) : (
            <p className="mt-3 text-xs text-gray-500 dark:text-dark-muted">
              No API secret set — the app connects in UUID-only mode (suitable for a trusted LAN).
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
