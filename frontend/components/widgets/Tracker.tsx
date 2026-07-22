'use client';

import type { Source, TrackerData } from '@/lib/widgets';
import { AccountBadge, SafeHtml, Sources, WidgetCard } from './Sources';

export function Tracker({
  data,
  sources,
  onTap,
}: {
  data: TrackerData;
  sources: Source[];
  onTap?: () => void;
}) {
  const positive = data.trade.unrealized_pnl >= 0;
  return (
    <button
      onClick={onTap}
      className="block w-full text-left active:scale-[0.99] transition-transform"
      type="button"
    >
      <WidgetCard eyebrow={`${data.ticker} · Trade-setup tracker`} headerRight={<AccountBadge label={data.account_label} />} className="cursor-pointer">
        <div className="flex items-center justify-between mb-2.5">
          <span className="text-[10px] font-bold tracking-[0.12em] uppercase text-green flex items-center gap-1.5">
            <span className="live-dot inline-block" />
            Deployed · armed
          </span>
          <div className="text-right">
            <div className={`text-[18px] font-semibold ${positive ? 'text-green' : 'text-red'}`}>
              {positive ? '+' : ''}${data.trade.unrealized_pnl.toFixed(2)}
            </div>
            <div className={`text-[11.5px] font-medium ${positive ? 'text-green' : 'text-red'}`}>
              {positive ? '+' : ''}{data.trade.unrealized_pnl_pct.toFixed(2)}% · unrealised
            </div>
          </div>
        </div>

        <div className="grid grid-cols-4 gap-2 bg-bg border border-border rounded-lg px-3 py-2.5 mb-3 text-left">
          <Cell label="Entry" value={`$${data.trade.fill_price}`} />
          <Cell label="Current" value={`$${data.trade.current_price}`} />
          {data.trade.sl !== undefined && <Cell label="SL" value={`$${data.trade.sl}`} accent="text-red" />}
          {data.trade.tp !== undefined && <Cell label="TP" value={`$${data.trade.tp}`} accent="text-green" />}
        </div>

        <div className="text-[12.5px] leading-snug text-text-2 mb-2.5">
          <strong className="text-text">TL;DR</strong> · <SafeHtml html={data.thesis_tldr_html} />
        </div>

        <div className="flex items-center justify-between px-2.5 py-2 bg-surface-2 rounded-lg text-xs font-medium text-accent">
          <span>Tap to read the full thesis</span>
          <span>→</span>
        </div>

        <Sources sources={sources} />
      </WidgetCard>
    </button>
  );
}

function Cell({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div>
      <div className="text-[9px] font-semibold uppercase tracking-[0.1em] text-text-3 mb-0.5">{label}</div>
      <div className={`text-[13px] font-semibold ${accent ?? ''}`}>{value}</div>
    </div>
  );
}
