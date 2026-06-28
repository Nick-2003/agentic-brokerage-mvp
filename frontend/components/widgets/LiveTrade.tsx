import type { LiveTradeData, Source } from '@/lib/widgets';
import { Sources, WidgetCard } from './Sources';

export function LiveTrade({ data, sources }: { data: LiveTradeData; sources: Source[] }) {
  const positive = data.unrealized_pnl >= 0;
  const sideLabel = data.side === 'short' ? 'Short' : 'Long';
  const sideClass = data.side === 'short' ? 'text-red' : 'text-green';
  const news = data.news_since_fill ?? [];

  return (
    <WidgetCard eyebrow="Live trade · armed">
      <div className="flex items-baseline gap-2 flex-wrap mb-1">
        <span className={`text-[22px] font-semibold -tracking-tight ${sideClass}`}>{sideLabel}</span>
        <span className="text-[22px] font-semibold -tracking-tight">{data.shares} {data.ticker}</span>
        <span className="text-[18px] font-semibold -tracking-tight text-text-2">
          @ {data.currency}{data.fill_price}
        </span>
      </div>
      <div className="text-[12.5px] text-text-2 mb-3">Filled · OCO bracket armed</div>

      {/* Live P&L row */}
      <div className="flex items-center gap-3 px-3 py-2.5 bg-surface-2 rounded-xl mb-3">
        <span className="live-dot" />
        <div className="flex-1 text-[13px]">
          <strong className={positive ? 'text-green' : 'text-red'}>
            {positive ? '+' : ''}{data.currency}{data.unrealized_pnl.toFixed(2)} ({positive ? '+' : ''}{data.unrealized_pnl_pct.toFixed(2)}%)
          </strong>{' '}
          unrealised
        </div>
        <div className="text-[11px] text-text-3">{data.currency}{data.current_price.toFixed(2)}</div>
      </div>

      {/* SL / TP cells */}
      <div className="grid grid-cols-2 gap-px bg-border rounded-xl overflow-hidden">
        {data.sl_armed_at && (
          <div className="bg-surface px-3.5 py-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-red">Stop loss · armed</div>
            <div className="text-[14px] font-semibold mt-0.5">{data.currency}{data.sl_armed_at}</div>
            <div className="text-[11px] text-text-3 mt-0.5">
              {(((data.sl_armed_at / data.current_price - 1) * 100)).toFixed(1)}% away
            </div>
          </div>
        )}
        {data.tp_armed_at && (
          <div className="bg-surface px-3.5 py-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-green">Take profit · armed</div>
            <div className="text-[14px] font-semibold mt-0.5">{data.currency}{data.tp_armed_at}</div>
            <div className="text-[11px] text-text-3 mt-0.5">
              +{(((data.tp_armed_at / data.current_price - 1) * 100)).toFixed(1)}% to go
            </div>
          </div>
        )}
      </div>

      {/* News since fill */}
      {news.length > 0 && (
        <div className="mt-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-text-3 mb-1.5">Since you bought</div>
          <ul className="space-y-1.5">
            {news.map((n, i) => (
              <li key={i} className="text-[12.5px] leading-snug">
                <span className="text-text">{n.headline}</span>
                <span className="text-text-3"> · {n.source}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <Sources sources={sources} />
    </WidgetCard>
  );
}
