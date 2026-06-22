'use client';

import { useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries,
  ColorType,
  LineSeries,
  LineStyle,
  createChart,
} from 'lightweight-charts';
import type { Source, TAChartData } from '@/lib/widgets';
import { fetchChartData, type Candle } from '@/lib/chart';
import { SafeHtml, Sources, WidgetCard } from './Sources';

// 054 — indicators are period-parameterised and rendered generically. The agent
// passes any of `SMA <n>` / `EMA <n>` / `RSI <n>` / `BB <n>` in `indicators_applied`;
// each is parsed, computed client-side from the candles, and drawn:
//   • SMA / EMA  → overlay line on the price pane
//   • BB         → upper/lower band (+ faint mid) on the price pane
//   • RSI        → its OWN pane below the price chart (0–100, with 30/70 guides)
// VWAP is intentionally not charted on the daily timeframe (the backend drops it).
type IndKind = 'SMA' | 'EMA' | 'RSI' | 'BB';
type IndPlan = { raw: string; kind: IndKind; period: number; color: string; label: string };

const OVERLAY_COLORS = ['#F59E0B', '#534AB7', '#0EA5E9', '#DB2777', '#0F6E56', '#9333EA'];
const RSI_COLOR = '#7C3AED';
const BB_COLOR = '#64748B';
const IND_RE = /^(SMA|EMA|RSI|BB)\s+(\d+)$/i;

// Parse `indicators_applied` → a render plan, assigning overlay colors in order.
// Unrecognized entries (e.g. VWAP) are skipped.
function planIndicators(applied: string[] | undefined): IndPlan[] {
  const out: IndPlan[] = [];
  let ci = 0;
  for (const raw of applied || []) {
    const m = IND_RE.exec((raw || '').trim());
    if (!m) continue;
    const kind = m[1].toUpperCase() as IndKind;
    const period = parseInt(m[2], 10);
    const label = `${kind} ${period}`;
    if (kind === 'RSI') out.push({ raw, kind, period, color: RSI_COLOR, label });
    else if (kind === 'BB') out.push({ raw, kind, period, color: BB_COLOR, label });
    else out.push({ raw, kind, period, color: OVERLAY_COLORS[ci++ % OVERLAY_COLORS.length], label });
  }
  return out;
}

export function TAChart({ data, sources }: { data: TAChartData; sources: Source[] }) {
  const r1 = data.key_levels.resistance[0];
  const s1 = data.key_levels.support[0];
  // 043: render in the ticker's own currency (e.g. HK$ for 1398.HK), not a hardcoded $.
  const ccy = data.currency ?? '$';
  const plan = planIndicators(data.indicators_applied);

  return (
    <WidgetCard eyebrow={`Technical · ${data.timeframe} · powered by TradingView`}>
      <div className="text-[15px] font-semibold mb-2">{data.ticker} · {data.timeframe} chart</div>

      <div className="bg-bg border border-border rounded-xl px-3.5 pt-3 pb-2.5 mb-3">
        {/* Chart slot (044/054): real client-side candle chart from our yfinance OHLCV
            with the requested indicators; falls back to the TradingView screenshot
            (local-TV path) then the SVG. */}
        <div className="flex items-center justify-between text-[11px] mb-2">
          <span className="font-semibold text-text text-[12px]">{ccy}{data.current_price.toFixed(2)}</span>
          <span className="text-text-3">{data.timeframe}</span>
        </div>
        <ChartSlot data={data} plan={plan} />
        {/* 054: legend reflects the ACTUAL requested indicators (was hardcoded SMA 50/200). */}
        <div className="flex flex-wrap gap-x-3.5 gap-y-1 text-[11px] text-text-2 mt-2">
          <Legend color="#534AB7" label="Price" />
          {plan.map((p) => (
            <Legend
              key={p.raw}
              color={p.color}
              label={p.kind === 'RSI' ? `${p.label} · lower pane` : p.label}
            />
          ))}
        </div>
      </div>

      {/* Key level cells */}
      <div className="flex gap-2 mb-3">
        <LevelCell label="Resistance" value={`${ccy}${r1}`} variant="r" />
        <LevelCell label="Support" value={`${ccy}${s1}`} variant="s" />
      </div>

      <div className="bg-surface-2 rounded-xl px-3.5 py-2.5 text-[13px] leading-snug text-text">
        <SafeHtml html={data.trend_summary_html} />
      </div>

      <Sources sources={sources} />
    </WidgetCard>
  );
}

type LinePoint = { time: string; value: number };

// --- indicator math (client-side, from candle closes) ------------------------
function sma(candles: Candle[], period: number): LinePoint[] {
  if (candles.length < period) return [];
  const out: LinePoint[] = [];
  let sum = 0;
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i].close;
    if (i >= period) sum -= candles[i - period].close;
    if (i >= period - 1) out.push({ time: candles[i].time, value: +(sum / period).toFixed(4) });
  }
  return out;
}

function ema(candles: Candle[], period: number): LinePoint[] {
  if (candles.length < period) return [];
  const k = 2 / (period + 1);
  let prev = 0;
  for (let i = 0; i < period; i++) prev += candles[i].close; // seed with SMA(period)
  prev /= period;
  const out: LinePoint[] = [{ time: candles[period - 1].time, value: +prev.toFixed(4) }];
  for (let i = period; i < candles.length; i++) {
    prev = candles[i].close * k + prev * (1 - k);
    out.push({ time: candles[i].time, value: +prev.toFixed(4) });
  }
  return out;
}

// Wilder's RSI — matches the backend's `_rsi_last`.
function rsi(candles: Candle[], period: number): LinePoint[] {
  if (candles.length < period + 1) return [];
  const val = (ag: number, al: number) => (al === 0 ? 100 : 100 - 100 / (1 + ag / al));
  let gain = 0, loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = candles[i].close - candles[i - 1].close;
    if (d >= 0) gain += d; else loss -= d;
  }
  let ag = gain / period, al = loss / period;
  const out: LinePoint[] = [{ time: candles[period].time, value: +val(ag, al).toFixed(2) }];
  for (let i = period + 1; i < candles.length; i++) {
    const d = candles[i].close - candles[i - 1].close;
    ag = (ag * (period - 1) + (d > 0 ? d : 0)) / period;
    al = (al * (period - 1) + (d < 0 ? -d : 0)) / period;
    out.push({ time: candles[i].time, value: +val(ag, al).toFixed(2) });
  }
  return out;
}

// Bollinger Bands (±2σ). Sample std (n−1) to match the backend's pandas `.std()`.
function bollinger(candles: Candle[], period: number, mult = 2):
  { upper: LinePoint[]; mid: LinePoint[]; lower: LinePoint[] } {
  const upper: LinePoint[] = [], mid: LinePoint[] = [], lower: LinePoint[] = [];
  if (candles.length < period || period < 2) return { upper, mid, lower };
  for (let i = period - 1; i < candles.length; i++) {
    let s = 0;
    for (let j = i - period + 1; j <= i; j++) s += candles[j].close;
    const m = s / period;
    let v = 0;
    for (let j = i - period + 1; j <= i; j++) { const d = candles[j].close - m; v += d * d; }
    const sd = Math.sqrt(v / (period - 1));
    const t = candles[i].time;
    mid.push({ time: t, value: +m.toFixed(4) });
    upper.push({ time: t, value: +(m + mult * sd).toFixed(4) });
    lower.push({ time: t, value: +(m - mult * sd).toFixed(4) });
  }
  return { upper, mid, lower };
}

// 044/054: the live chart. Fetches daily candles from /api/chart-data on mount and
// renders them with lightweight-charts (client-side, no local TV). Draws each
// indicator from `plan`. Falls back to the screenshot then the inline SVG.
function ChartSlot({ data, plan }: { data: TAChartData; plan: IndPlan[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [candles, setCandles] = useState<Candle[] | null>(null);

  useEffect(() => {
    let alive = true;
    setCandles(null);
    fetchChartData(data.ticker, data.timeframe).then((d) => {
      if (alive) setCandles(d?.candles ?? []);
    });
    return () => {
      alive = false;
    };
  }, [data.ticker, data.timeframe]);

  const hasRsi = plan.some((p) => p.kind === 'RSI');

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !candles || candles.length === 0) return;

    const chart = createChart(el, {
      width: el.clientWidth,
      height: hasRsi ? 240 : 160,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#6b7280', attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(0,0,0,0.05)' } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      handleScroll: false,
      handleScale: false,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#0F6E56', downColor: '#C0392B',
      wickUpColor: '#0F6E56', wickDownColor: '#C0392B', borderVisible: false,
    });
    candleSeries.setData(
      candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }))
    );

    const lineOpts = (color: string, extra: Record<string, unknown> = {}) => ({
      color, lineWidth: 1 as const, priceLineVisible: false, lastValueVisible: false, ...extra,
    });

    for (const p of plan) {
      if (p.kind === 'SMA') {
        chart.addSeries(LineSeries, lineOpts(p.color)).setData(sma(candles, p.period));
      } else if (p.kind === 'EMA') {
        chart.addSeries(LineSeries, lineOpts(p.color)).setData(ema(candles, p.period));
      } else if (p.kind === 'BB') {
        const b = bollinger(candles, p.period);
        chart.addSeries(LineSeries, lineOpts(p.color, { lineStyle: LineStyle.Dashed })).setData(b.upper);
        chart.addSeries(LineSeries, lineOpts(p.color, { lineStyle: LineStyle.Dashed })).setData(b.lower);
        chart.addSeries(LineSeries, lineOpts('rgba(100,116,139,0.5)')).setData(b.mid);
      } else if (p.kind === 'RSI') {
        // Separate pane below the price chart (lightweight-charts v5 panes).
        const rsiSeries = chart.addSeries(LineSeries, lineOpts(p.color), 1);
        rsiSeries.setData(rsi(candles, p.period));
        rsiSeries.createPriceLine({ price: 70, color: 'rgba(0,0,0,0.18)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
        rsiSeries.createPriceLine({ price: 30, color: 'rgba(0,0,0,0.18)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });
      }
    }

    // Keep the RSI pane compact so the price pane stays dominant.
    if (hasRsi) {
      try {
        const panes = chart.panes();
        if (panes.length > 1) panes[1].setHeight(64);
      } catch { /* older lib without panes() — non-fatal */ }
    }

    for (const r of data.key_levels.resistance || []) {
      candleSeries.createPriceLine({ price: r, color: '#C0392B', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'R' });
    }
    for (const s of data.key_levels.support || []) {
      candleSeries.createPriceLine({ price: s, color: '#0F6E56', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'S' });
    }

    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [candles, hasRsi, plan, data.key_levels]);

  if (candles === null || candles.length > 0) {
    return <div ref={containerRef} className="w-full" style={{ height: hasRsi ? 240 : 160 }} />;
  }
  if (typeof data.screenshot_url === 'string' && data.screenshot_url.length > 0) {
    return (
      <img
        src={data.screenshot_url}
        alt={`${data.ticker} ${data.timeframe} chart`}
        loading="lazy"
        className="w-full block rounded-md"
      />
    );
  }
  return <MockChartSvg />;
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="block w-3 h-0.5 rounded" style={{ backgroundColor: color }} />
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
  // Static-feeling chart drawing — graceful fallback when no real candles are
  // available (mock mode, or real data unreachable).
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
    </svg>
  );
}
