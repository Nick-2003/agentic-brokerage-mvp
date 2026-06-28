'use client';

import type { OrderTicketData, Source } from '@/lib/widgets';
import { SafeHtml, Sources, WidgetCard } from './Sources';

type Props = {
  data: OrderTicketData;
  sources: Source[];
  onConfirm?: () => void;
  onEdit?: () => void;
};

export function OrderTicket({ data, sources, onConfirm, onEdit }: Props) {
  const sideClass = data.side === 'sell' ? 'text-red' : 'text-green';
  const sideLabel = data.side === 'sell' ? 'Sell' : 'Buy';

  return (
    <WidgetCard eyebrow="Order · pending your review">
      <div className="flex items-baseline gap-2 flex-wrap mb-1">
        <span className={`text-[22px] font-semibold -tracking-tight ${sideClass}`}>{sideLabel}</span>
        <span className="text-[22px] font-semibold -tracking-tight">{data.shares} {data.ticker}</span>
        <span className="text-[18px] font-semibold -tracking-tight text-text-2">
          @ {data.currency}{data.limit_price}
        </span>
      </div>
      <div className="text-[12.5px] text-text-2 mb-3.5">
        Limit order · day · routed to best venue
        {data.bracket_source === 'from_prompt' && ' · TP/SL from your prompt'}
      </div>

      {/* Top row: notional + position size */}
      <div className="grid grid-cols-2 gap-px bg-border rounded-xl overflow-hidden mb-3.5">
        <Cell label="Notional" value={`${data.currency}${data.notional.toLocaleString()}`} sub={data.portfolio_pct ? `${data.portfolio_pct}% of NAV` : undefined} />
        <Cell label="Position size" value={`${data.shares} shares`} sub={data.within_risk_rule === false ? '⚠ Above your 2% rule' : 'Within 2% rule'} />
      </div>

      {/* TP/SL row */}
      {(data.tp_price || data.sl_price) && (
        <div className="mb-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-3 mb-2">
            Risk management · TP / SL
            {data.bracket_source === 'from_prompt' && (
              <span className="text-accent ml-2 normal-case tracking-normal">· from your prompt</span>
            )}
          </div>
          <div className="grid grid-cols-2 gap-px bg-border rounded-xl overflow-hidden">
            {data.sl_price && (
              <Cell
                label="Stop loss"
                labelColor="text-red"
                value={`${data.currency}${data.sl_price}`}
                sub={`-${(((data.limit_price - data.sl_price) / data.limit_price) * 100).toFixed(1)}% · risk ${data.currency}${data.risk_amount?.toLocaleString() ?? '—'}`}
              />
            )}
            {data.tp_price && (
              <Cell
                label="Take profit"
                labelColor="text-green"
                value={`${data.currency}${data.tp_price}`}
                sub={`+${(((data.tp_price - data.limit_price) / data.limit_price) * 100).toFixed(1)}% · reward ${data.currency}${data.reward_amount?.toLocaleString() ?? '—'}`}
              />
            )}
          </div>
          {data.rr_ratio && (
            <div className="mt-2 px-3 py-2 bg-accent-bg rounded-lg text-xs text-text">
              <strong className="text-accent">R:R {data.rr_ratio.toFixed(1)}x</strong> · OCO bracket — both legs cancel if one fills
            </div>
          )}
        </div>
      )}

      {/* Notes */}
      {data.notes_html && (
        <div className="text-[12.5px] leading-snug text-text-2 mb-3">
          <SafeHtml html={data.notes_html} />
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 mt-3">
        <button
          onClick={onEdit}
          className="flex-1 py-2.5 rounded-full text-[13px] font-semibold bg-bg border border-border text-text active:scale-95 transition-transform"
        >
          Edit TP/SL
        </button>
        <button
          onClick={onConfirm}
          className="flex-1 py-2.5 rounded-full text-[13px] font-semibold bg-text text-white active:scale-95 transition-transform"
        >
          Confirm trade
        </button>
      </div>

      <Sources sources={sources} />
    </WidgetCard>
  );
}

function Cell({
  label,
  value,
  sub,
  labelColor = 'text-text-3',
}: {
  label: string;
  value: string;
  sub?: string;
  labelColor?: string;
}) {
  return (
    <div className="bg-surface px-3.5 py-2.5">
      <div className={`text-[10px] font-semibold uppercase tracking-[0.1em] ${labelColor}`}>{label}</div>
      <div className="text-[14px] font-semibold mt-0.5">{value}</div>
      {sub && <div className="text-[11px] text-text-3 mt-0.5">{sub}</div>}
    </div>
  );
}
