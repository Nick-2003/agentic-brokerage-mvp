'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  brokerageErrorMessage,
  createSnapTradeSession,
  getBrokerageState,
  selectBrokerAccount,
  type BrokerageState,
} from '@/lib/brokerage';
import {
  trackBrokerAccountSelected,
  trackBrokerConnectionFailed,
  trackBrokerConnectionStarted,
} from '@/lib/analytics';

export default function SnapTradeConnection({ token }: { token: string }) {
  const [state, setState] = useState<BrokerageState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    const result = await getBrokerageState(token);
    if (result.ok) {
      setState(result.data);
      setError(null);
    } else {
      setError(result.error);
    }
    setLoading(false);
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const connection = useMemo(
    () => state?.connections.find((item) => item.provider === 'snaptrade') ?? null,
    [state]
  );
  const accounts = useMemo(
    () =>
      connection
        ? (state?.accounts ?? []).filter((item) => item.connection_id === connection.id)
        : [],
    [connection, state]
  );

  async function openPortal() {
    setBusy(true);
    setError(null);
    trackBrokerConnectionStarted('snaptrade');
    const result = await createSnapTradeSession(token);
    if (!result.ok) {
      setBusy(false);
      setError(result.error);
      trackBrokerConnectionFailed('snaptrade', result.error);
      return;
    }
    // Short-lived portal URL: navigate immediately; never persist or send to analytics.
    window.location.assign(result.data.portal_url);
  }

  async function select(accountId: string) {
    setBusy(true);
    setError(null);
    const result = await selectBrokerAccount(token, accountId);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      trackBrokerConnectionFailed('snaptrade', result.error);
      return;
    }
    setState(result.data.state);
    trackBrokerAccountSelected('snaptrade');
  }

  return (
    <section className="bg-bg rounded-3xl shadow-2xl p-6 text-text" aria-labelledby="snaptrade-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="snaptrade-title" className="text-base font-semibold">
            Brokerage account
          </h2>
          <p className="mt-1 text-[13px] text-text-3">
            Connect through SnapTrade with read-only access, then choose one account for portfolio
            sizing.
          </p>
        </div>
        {connection && (
          <span className="shrink-0 rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-text-2">
            {connection.status}
          </span>
        )}
      </div>

      {loading ? (
        <p className="mt-4 text-[13px] text-text-3">Loading brokerage accounts…</p>
      ) : !connection || connection.status !== 'active' ? (
        <div className="mt-4">
          <button
            type="button"
            onClick={openPortal}
            disabled={busy}
            className="h-12 w-full rounded-2xl bg-accent text-sm font-semibold text-white transition-transform active:scale-[0.98] disabled:opacity-50"
          >
            {busy ? 'Opening SnapTrade…' : connection ? 'Continue or reconnect' : 'Connect brokerage'}
          </button>
          <p className="mt-2 text-[11px] text-text-3">
            Your SnapTrade application keys remain on the backend. This page never receives them.
          </p>
        </div>
      ) : accounts.length === 0 ? (
        <div className="mt-4 space-y-2">
          <p className="text-[13px] text-text-3">The connection is active, but accounts are still syncing.</p>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={busy}
            className="h-11 w-full rounded-2xl border border-border text-sm font-semibold disabled:opacity-50"
          >
            Refresh accounts
          </button>
        </div>
      ) : (
        <div className="mt-4 space-y-2">
          <p className="text-[12px] font-medium text-text-2">Portfolio sizing account</p>
          {accounts.map((account) => (
            <button
              type="button"
              key={account.id}
              onClick={() => void select(account.id)}
              disabled={busy || account.status !== 'active' || account.is_selected}
              aria-pressed={account.is_selected}
              className={`flex w-full items-center justify-between rounded-2xl border p-3 text-left transition-colors disabled:opacity-70 ${
                account.is_selected ? 'border-accent bg-surface' : 'border-border hover:border-accent'
              }`}
            >
              <span>
                <span className="block text-sm font-medium">{account.masked_name}</span>
                <span className="block text-[11px] text-text-3">
                  {account.base_currency} · {account.status}
                </span>
              </span>
              <span className="text-[11px] font-semibold text-accent">
                {account.is_selected ? 'Selected' : 'Select'}
              </span>
            </button>
          ))}
          {!accounts.some((account) => account.is_selected) && (
            <p className="text-[11px] text-amber">
              Select one account before SnapTrade can supply portfolio sizing data.
            </p>
          )}
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={busy}
            className="w-full pt-1 text-center text-[12px] text-text-3 underline underline-offset-2 hover:text-text-2 disabled:opacity-50"
          >
            Refresh status
          </button>
        </div>
      )}

      {error && (
        <div role="alert" className="mt-3 text-[12px] text-red">
          {brokerageErrorMessage(error)}
        </div>
      )}
    </section>
  );
}
