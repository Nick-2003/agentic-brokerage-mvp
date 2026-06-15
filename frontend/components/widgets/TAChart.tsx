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

export function TAChart({ data, sources }: { data: TAChartData; sources: Source[] }) {
  const r1 = data.key_levels.resistance[0];
  const s1 = data.key_levels.support[0];
  // 043: render in the ticker's own currency (e.g. HK$ for 1398.HK), not a hardcoded $.
  const ccy = data.currency ?? '$';

  return (
    <WidgetCard eyebrow={`Technical · ${data.timeframe} · powered by TradingView`}>
      <div className="text-[15px] font-semibold mb-2">{data.ticker} · {data.timeframe} chart</div>

      <div className="bg-bg border border-border rounded-xl px-3.5 pt-3 pb-2.5 mb-3">
        {/* Chart slot (044): real client-side candle chart from our yfinance OHLCV;
            falls back to the TradingView screenshot (local-TV path) then the SVG. */}
        <div className="flex items-center justify-between text-[11px] mb-2">
          <span className="font-semibold text-text text-[12px]">{ccy}{data.current_price.toFixed(2)}</span>
          <span className="text-text-3">{data.timeframe}</span>
        </div>
        <ChartSlot data={data} />
        <div className="flex gap-3.5 text-[11px] text-text-2 mt-2">
          <Legend swatch="bg-accent" label="Price" />
          {data.indicators_applied.includes('SMA 50') && <Legend swatch="bg-amber-DEFAULT" label="SMA 50" />}
          {data.indicators_applied.includes('SMA 200') && <Legend swatch="bg-green-DEFAULT" label="SMA 200" />}
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

// Simple moving average over candle closes → lightweight-charts line points.
function sma(candles: Candle[], period: number): { time: string; value: number }[] {
  if (candles.length < period) return [];
  const out: { time: string; value: number }[] = [];
  let sum = 0;
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i].close;
    if (i >= period) sum -= candles[i - period].close;
    if (i >= period - 1) out.push({ time: candles[i].time, value: +(sum / period).toFixed(4) });
  }
  return out;
}

// 044: the live chart. Fetches daily candles from /api/chart-data on mount and
// renders them with TradingView's lightweight-charts (client-side, no local TV).
// Falls back to the TradingView screenshot (local-TV path) then the inline SVG
// when no candles are available. Chart instance is created in an effect and
// removed on unmount, so a pinned chart survives re-mount.
function ChartSlot({ data }: { data: TAChartData }) {
  const containerRef = useRef<HTMLDivElement>(null);
  // null = still loading; [] = loaded but no series (→ fallback); else candles.
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

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !candles || candles.length === 0) return;

    const chart = createChart(el, {
      width: el.clientWidth,
      height: 160,
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

    const applied = data.indicators_applied || [];
    if (applied.includes('SMA 50')) {
      chart.addSeries(LineSeries, { color: '#F59E0B', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
        .setData(sma(candles, 50));
    }
    if (applied.includes('SMA 200')) {
      chart.addSeries(LineSeries, { color: '#534AB7', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
        .setData(sma(candles, 200));
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
  }, [candles, data.indicators_applied, data.key_levels]);

  if (candles === null || candles.length > 0) {
    // Loading OR have data → the chart container (filled by the effect once loaded;
    // a thin skeleton height while the fetch is in flight).
    return <div ref={containerRef} className="w-full h-[160px]" />;
  }
  // Loaded but empty → screenshot (local-TV path) if present, else the inline SVG.
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
