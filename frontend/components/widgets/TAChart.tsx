import type { Source, TAChartData } from '@/lib/widgets';
import { SafeHtml, Sources, WidgetCard } from './Sources';

export function TAChart({ data, sources }: { data: TAChartData; sources: Source[] }) {
  const r1 = data.key_levels.resistance[0];
  const s1 = data.key_levels.support[0];
  // Bug fix from proposal 002 §4.8: the prior version rendered <MockChartSvg /> unconditionally
  // and ignored `data.screenshot_url`. Even when the backend returned a real TradingView
  // screenshot (base64 data URL), the user saw the mock SVG. Now render the screenshot when
  // present, fall back to the inline SVG otherwise.
  const hasScreenshot = typeof data.screenshot_url === 'string' && data.screenshot_url.length > 0;

  return (
    <WidgetCard eyebrow={`Technical · ${data.timeframe} · powered by TradingView`}>
      <div className="text-[15px] font-semibold mb-2">{data.ticker} · {data.timeframe} chart</div>

      <div className="bg-bg border border-border rounded-xl px-3.5 pt-3 pb-2.5 mb-3">
        {/* Chart slot. When TradingView MCP is live, this is the real screenshot. */}
        <div className="flex items-center justify-between text-[11px] mb-2">
          <span className="font-semibold text-text text-[12px]">${data.current_price.toFixed(2)}</span>
          <span className="text-text-3">{data.timeframe}</span>
        </div>
        {hasScreenshot ? (
          <img
            src={data.screenshot_url}
            alt={`${data.ticker} ${data.timeframe} chart`}
            loading="lazy"
            className="w-full block rounded-md"
          />
        ) : (
          <MockChartSvg />
        )}
        <div className="flex gap-3.5 text-[11px] text-text-2 mt-2">
          <Legend swatch="bg-accent" label="Price" />
          {data.indicators_applied.includes('SMA 50') && <Legend swatch="bg-amber-DEFAULT" label="SMA 50" />}
          {data.indicators_applied.includes('SMA 200') && <Legend swatch="bg-green-DEFAULT" label="SMA 200" />}
        </div>
      </div>

      {/* Key level cells */}
      <div className="flex gap-2 mb-3">
        <LevelCell label="Resistance" value={`$${r1}`} variant="r" />
        <LevelCell label="Support" value={`$${s1}`} variant="s" />
      </div>

      <div className="bg-surface-2 rounded-xl px-3.5 py-2.5 text-[13px] leading-snug text-text">
        <SafeHtml html={data.trend_summary_html} />
      </div>

      <Sources sources={sources} />
    </WidgetCard>
  );
}

function Legend({ swatch, label }: { swatch: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={`block w-3 h-0.5 rounded ${swatch}`} />
      {label}
    </span>
  );
}

function LevelCell({ label, value, variant }: { label: string; value: string; variant: 'r' | 's' }) {
  const bg = variant === 'r' ? 'bg-red-bg/40 border-red-DEFAULT/20' : 'bg-green-bg/50 border-green-DEFAULT/20';
  const labelColor = variant === 'r' ? 'text-red-DEFAULT' : 'text-green-DEFAULT';
  return (
    <div className={`flex-1 rounded-lg border ${bg} px-2.5 py-2 text-center`}>
      <div className={`text-[9.5px] font-semibold uppercase tracking-[0.1em] ${labelColor}`}>{label}</div>
      <div className="text-[15px] font-semibold mt-0.5">{value}</div>
    </div>
  );
}

function MockChartSvg() {
  // Static-feeling animated chart drawing — same shape as the demo's golden cross chart.
  // Used as a graceful fallback when no real screenshot URL is present (mock mode,
  // or real MCP unreachable).
  return (
    <svg viewBox="0 0 300 140" preserveAspectRatio="none" className="w-full h-[140px] block">
      <path
        d="M 0 110 L 20 95 L 40 100 L 60 80 L 80 85 L 100 70 L 120 75 L 140 60 L 160 55 L 180 45 L 200 50 L 220 40 L 240 30 L 260 35 L 280 22 L 300 18 L 300 140 L 0 140 Z"
        fill="rgba(83,74,183,0.08)"
      />
      <path
        d="M 0 110 L 20 95 L 40 100 L 60 80 L 80 85 L 100 70 L 120 75 L 140 60 L 160 55 L 180 45 L 200 50 L 220 40 L 240 30 L 260 35 L 280 22 L 300 18"
        fill="none"
        stroke="#534AB7"
        strokeWidth="2"
      />
      <path
        d="M 0 105 L 30 100 L 60 95 L 90 88 L 120 78 L 150 68 L 180 58 L 210 50 L 240 42 L 270 32 L 300 25"
        fill="none"
        stroke="#F59E0B"
        strokeWidth="1.5"
      />
      <path
        d="M 0 90 L 30 88 L 60 85 L 90 82 L 120 78 L 150 73 L 180 67 L 210 60 L 240 52 L 270 45 L 300 38"
        fill="none"
        stroke="#0F6E56"
        strokeWidth="1.5"
      />
    </svg>
  );
}
