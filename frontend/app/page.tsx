'use client';

import { AuthGate } from '@/components/AuthGate';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
} from 'lightweight-charts';

type AssetKey = 'MU' | 'GLD' | 'SPY';
type Bias = 'bullish' | 'bearish' | 'no_trade';
type Side = Exclude<Bias, 'no_trade'>;
type LocationQuality = 'clean' | 'extended' | 'messy';
type Detail = 'why' | 'sources' | 'breaks' | null;
type Phase =
  | 'empty'
  | 'brief_work'
  | 'brief'
  | 'preflight'
  | 'catalyst'
  | 'macro_work'
  | 'macro'
  | 'direction'
  | 'technical_work'
  | 'technical'
  | 'setup_work'
  | 'levels'
  | 'final';

type Levels = {
  entry: number;
  target: number;
  stop: number;
};

type DemoCandle = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type ActiveRun = {
  key: string;
  status: string;
  title: string;
  lines: string[];
  sources: string[];
  conclusion: string;
  nextPhase: Phase;
  durationMs?: number;
  minDurationMs?: number;
};

type SetupPlan = Levels & {
  score: number;
  verdict: 'clean' | 'messy' | 'too early';
  thesis: string;
  evidence: string[];
  invalidation: string[];
};

type DemoAsset = {
  ticker: AssetKey;
  label: string;
  name: string;
  last: number;
  changePct: number;
  catalyst: string;
  catalystRisk: string;
  macroRead: string;
  macroEvent: string;
  macroVerdict: string;
  agentRead: string;
  mainRisk: string;
  recommendedBias: Side;
  bullSummary: string;
  bearSummary: string;
  technicalRead: string;
  technicalTrigger: string;
  supports: number[];
  resistances: number[];
  sources: string[];
  bull: SetupPlan;
  bear: SetupPlan;
};

type MacroFactor = {
  label: string;
  value: number;
  decimals: number;
  change: string;
  tone: 'green' | 'blue' | 'muted';
  points: number[];
};

type LiveMacroFactor = MacroFactor & {
  flash: 'up' | 'down' | null;
};

type PickerInstrument = {
  ticker: AssetKey;
  name: string;
  logoUrl: string | null;
};

type TickerIdea = PickerInstrument & {
  score: number;
  price: string;
  change: string;
  direction: 'Bullish' | 'Selective' | 'Defensive';
  read: string;
  chart: number[];
  factors: Array<{
    label: string;
    read: string;
  }>;
};

type ThesisSection = {
  label: 'One line overview' | 'Catalyst' | 'Key risks';
  text: string;
};

const ASSETS: Record<AssetKey, DemoAsset> = {
  MU: {
    ticker: 'MU',
    label: 'Micron',
    name: 'Micron Technology, Inc.',
    last: 132.4,
    changePct: 1.8,
    catalyst: 'The live reason to care is memory pricing and HBM demand. MU is trading like the market wants AI server exposure beyond NVDA.',
    catalystRisk: 'If semis lose leadership, this becomes a crowded momentum trade with weak downside liquidity.',
    macroRead: 'Risk appetite is still supportive, but this is high beta. I would not force size if rates or Nasdaq breadth turn lower.',
    macroEvent: 'Next macro checkpoint: rates, dollar, and broad tech breadth before the next session open.',
    macroVerdict: 'Backdrop helps the long, but only with a tight invalidation level.',
    agentRead: 'MU is tradable, but only if it holds the breakout shelf above 128.',
    mainRisk: 'A failed breakout would trap momentum buyers and pull price back into the prior range.',
    recommendedBias: 'bullish',
    bullSummary: 'My lean is bullish while MU holds above 128 and semis keep leading.',
    bearSummary: 'The bear case starts if 128 fails and the breakout becomes a trap.',
    technicalRead: 'Price is above the breakout shelf. I care most about whether buyers defend 128 on the first pullback.',
    technicalTrigger: 'Clean trigger: hold 128, reclaim intraday strength, then take the long near 133.',
    supports: [128.2, 121.5, 116.8],
    resistances: [136.5, 142.0, 148.5],
    sources: ['Quote tape', 'Semis breadth', 'Level map'],
    bull: {
      entry: 133.1,
      target: 145.8,
      stop: 127.6,
      score: 78,
      verdict: 'clean',
      thesis: 'Buy continuation only if MU accepts above the breakout shelf and volume confirms.',
      evidence: [
        'Price is holding above prior supply instead of rejecting it.',
        'Semiconductor beta gives the setup a supportive tape.',
        'Stop can sit close to invalidation, so risk is defined.',
      ],
      invalidation: ['Lose 128 on a closing basis.', 'Semis roll over while MU fails to reclaim the prior day high.'],
    },
    bear: {
      entry: 127.8,
      target: 117.2,
      stop: 133.6,
      score: 64,
      verdict: 'messy',
      thesis: 'Short only on a failed breakout and lower high, not into strength.',
      evidence: [
        'The long side is crowded after the recent move.',
        'A break under support would turn the breakout into a bull trap.',
        'Downside target lines up with the prior demand zone.',
      ],
      invalidation: ['Reclaim 134 with expanding volume.', 'Sector breadth improves across memory and AI hardware.'],
    },
  },
  GLD: {
    ticker: 'GLD',
    label: 'Gold',
    name: 'SPDR Gold Shares',
    last: 228.6,
    changePct: -0.3,
    catalyst: 'The trade is alive because gold is balancing real-rate uncertainty against persistent hedge demand.',
    catalystRisk: 'If the dollar and real yields both firm, gold can chop sideways and reject breakouts.',
    macroRead: 'Macro is mixed. This is not a momentum chase. It needs confirmation above range resistance.',
    macroEvent: 'Next macro checkpoint: dollar, real yields, Fed commentary, and risk-off flows.',
    macroVerdict: 'Backdrop is acceptable, but the setup needs patience.',
    agentRead: 'GLD is range-bound. I would only chase it if it clears 231 cleanly.',
    mainRisk: 'False breaks are common when gold lacks a clean macro impulse.',
    recommendedBias: 'bullish',
    bullSummary: 'My lean is cautiously bullish only above 231.',
    bearSummary: 'The bear case is a failed range and a break below 225.',
    technicalRead: 'The chart is boxed between 225 support and 231 resistance. The edge is at the boundary, not the middle.',
    technicalTrigger: 'Clean trigger: close above 231 and hold the retest. Otherwise, no chase.',
    supports: [225.4, 221.0, 216.8],
    resistances: [231.2, 236.4, 240.0],
    sources: ['Macro board', 'Dollar/rates check', 'Range levels'],
    bull: {
      entry: 230.8,
      target: 239.4,
      stop: 224.9,
      score: 71,
      verdict: 'clean',
      thesis: 'Buy strength only if GLD clears range resistance and holds the breakout retest.',
      evidence: [
        'Resistance is close enough to demand confirmation first.',
        'The stop sits below range support, making invalidation simple.',
        'The asset can hedge risk if equities lose momentum.',
      ],
      invalidation: ['Breakout fails back below 225.5.', 'Real yields and the dollar both push higher.'],
    },
    bear: {
      entry: 224.7,
      target: 216.9,
      stop: 231.5,
      score: 58,
      verdict: 'too early',
      thesis: 'Short only on a range breakdown, because gold is still a macro hedge.',
      evidence: [
        'A loss of 225 would break the near-term demand shelf.',
        'Trend has stalled, so downside can open if macro support fades.',
        'Target sits ahead of the next major support cluster.',
      ],
      invalidation: ['Reclaim 231 and hold above it.', 'Gold catches a safe-haven bid on risk-off news.'],
    },
  },
  SPY: {
    ticker: 'SPY',
    label: 'SPY',
    name: 'SPDR S&P 500 ETF Trust',
    last: 583.2,
    changePct: 0.32,
    catalyst: 'The trade is alive because the index keeps grinding higher while dips remain shallow.',
    catalystRisk: 'Leadership is narrow. If mega-cap tech breaks, the index can de-risk faster than the surface volatility suggests.',
    macroRead: 'The regime still supports dip buying, but positioning is complacent near highs.',
    macroEvent: 'Next macro checkpoint: rates, breadth, and mega-cap earnings revisions.',
    macroVerdict: 'Backdrop supports a long, but only if support holds.',
    agentRead: 'SPY is still constructive, but the trade needs to stay close to support.',
    mainRisk: 'A low-vol market can break quickly if leadership fails.',
    recommendedBias: 'bullish',
    bullSummary: 'My lean is bullish while SPY holds its higher-low structure above 578.',
    bearSummary: 'The bear case starts on a failed retest below 578.',
    technicalRead: 'The chart is orderly. I would buy near support, not chase into the 600 magnet.',
    technicalTrigger: 'Clean trigger: defend 578, reclaim 584, target before 600.',
    supports: [578.0, 571.5, 564.0],
    resistances: [587.5, 593.0, 600.0],
    sources: ['Index tape', 'Breadth check', 'Rates sensitivity'],
    bull: {
      entry: 584.0,
      target: 596.5,
      stop: 577.6,
      score: 74,
      verdict: 'clean',
      thesis: 'Buy a controlled continuation setup while the index holds the higher-low structure.',
      evidence: [
        'Trend is still up and pullbacks remain shallow.',
        'Entry is close enough to support to avoid chasing.',
        'First target leaves room before the 600 psychological level.',
      ],
      invalidation: ['Lose 578 and fail to reclaim it.', 'Market breadth deteriorates for consecutive sessions.'],
    },
    bear: {
      entry: 577.4,
      target: 565.5,
      stop: 584.8,
      score: 62,
      verdict: 'messy',
      thesis: 'Short only if SPY loses support and rallies fail below it.',
      evidence: [
        'Positioning is stretched enough that failed support can move quickly.',
        'Stop placement is clear above the lost support zone.',
        'Target maps to the next visible demand area.',
      ],
      invalidation: ['Reclaim 585 with broad participation.', 'Volatility compresses while cyclicals improve.'],
    },
  },
};

const MARKET_BRIEF = {
  title: "Today's brief: Bullish",
  sources: ['Index trend', 'Volatility', 'Rates', 'Dollar', 'Sector leadership', 'Liquidity', 'Calendar risk'],
  bullets: [
    'Risk appetite is positive as tech and AI earnings keep buyers engaged.',
    'Today’s key events: employment-cost inflation ran firm, Chicago PMI beat, and consumer sentiment improved.',
    'Stay selective. Higher yields, a firm dollar, and oil pressure make weak breakouts easy to fade.',
  ],
};

const DAILY_BRIEF_FACTORS = [
  { label: 'Index trend', read: 'SPY closed higher and QQQ led, but IWM lagged. Bias is risk-on, not broad risk-on.' },
  { label: 'Volatility', read: 'VIX eased near 16.8. That gives breakouts room, but complacency argues against chasing late entries.' },
  { label: 'Rates', read: 'Treasury yields pushed higher and TLT sold off. That is the main pressure point for growth and momentum trades.' },
  { label: 'Dollar', read: 'Dollar proxy UUP finished slightly green. A firmer dollar keeps pressure on commodities and high-beta risk.' },
  { label: 'Sector leadership', read: 'Leadership is concentrated in mega-cap tech and AI after Amazon strength. Semis still have permission if breadth holds.' },
  { label: 'Liquidity', read: 'SPY, QQQ, and IWM all traded heavy volume. Execution is fine, but opening-range confirmation still matters.' },
  { label: 'Calendar risk', read: 'The market is digesting Fed hold, inflation data, and earnings. Next week’s jobs data can reset the rate path.' },
];

const MACRO_FACTORS: MacroFactor[] = [
  { label: 'SP500', value: 7490, decimals: 0, change: '+0.7%', tone: 'green', points: [18, 30, 24, 36, 28, 40, 35, 44, 30, 27, 38] },
  { label: 'VIX', value: 15.95, decimals: 2, change: '-6.7%', tone: 'green', points: [40, 31, 36, 27, 30, 34, 46, 42, 48, 35, 22] },
  { label: 'NASDAQ', value: 25373, decimals: 0, change: '+1.0%', tone: 'green', points: [42, 38, 40, 32, 45, 39, 43, 35, 30, 22, 33] },
  { label: 'DXY', value: 100, decimals: 2, change: '-0.0%', tone: 'blue', points: [34, 36, 39, 38, 42, 45, 43, 44, 28, 22, 24] },
  { label: 'US10Y', value: 4.74, decimals: 2, change: '+1.7%', tone: 'blue', points: [22, 27, 34, 30, 36, 33, 38, 35, 40, 37, 46] },
];

const TICKER_IDEAS: TickerIdea[] = [
  {
    ticker: 'MU',
    name: 'Micron Technology, Inc.',
    logoUrl: 'https://icons.duckduckgo.com/ip3/micron.com.ico',
    score: 82,
    price: '$110.06',
    change: '+7.55%',
    direction: 'Bullish',
    read: 'Best tactical candidate: live AI-memory attention, strong retail interest, and enough volatility for a defined setup.',
    chart: [24, 27, 25, 31, 35, 33, 42, 39, 46, 50, 48, 54],
    factors: [
      { label: 'Earnings', read: 'Post-earnings momentum remains the cleanest reason to care.' },
      { label: 'Guidance', read: 'HBM and AI-server demand keep forward estimates in focus.' },
      { label: 'Analysts', read: 'Street tone is constructive, but the move is now crowded.' },
      { label: 'Macro sensitivity', read: 'High beta: works best while QQQ and semis keep leading.' },
      { label: 'Sector sympathy', read: 'AI hardware leadership supports the long side.' },
      { label: 'Unusual volume', read: 'Heavy tape confirms institutions and retail are both involved.' },
      { label: 'News catalyst', read: 'Memory pricing and AI capex are the active narrative.' },
      { label: 'Social attention', read: 'Ranked first on today’s WSB trend scan.' },
      { label: 'Fresh or crowded', read: 'Fresh enough to trade, crowded enough to require a tight stop.' },
    ],
  },
  {
    ticker: 'SPY',
    name: 'SPDR S&P 500 ETF Trust',
    logoUrl: 'https://icons.duckduckgo.com/ip3/spdrs.com.ico',
    score: 74,
    price: '$640.00',
    change: '+0.64%',
    direction: 'Selective',
    read: 'Clean index expression if the user wants market direction without single-name catalyst risk.',
    chart: [34, 36, 35, 38, 41, 40, 43, 45, 44, 47, 46, 49],
    factors: [
      { label: 'Earnings', read: 'Index is absorbing mega-cap earnings instead of breaking down.' },
      { label: 'Guidance', read: 'Forward read depends on whether AI/tech revisions keep holding up.' },
      { label: 'Analysts', read: 'Less relevant than breadth and index-level positioning.' },
      { label: 'Macro sensitivity', read: 'Sensitive to rates, dollar, and next jobs-data repricing.' },
      { label: 'Sector sympathy', read: 'Mega-cap tech is carrying more weight than small caps.' },
      { label: 'Unusual volume', read: 'Liquidity is strong, execution quality is high.' },
      { label: 'News catalyst', read: 'Fed hold, inflation prints, and earnings are the main inputs.' },
      { label: 'Social attention', read: 'Top-three WSB trend scan, useful as a broad tape proxy.' },
      { label: 'Fresh or crowded', read: 'Not fresh, but still controlled while support holds.' },
    ],
  },
  {
    ticker: 'GLD',
    name: 'SPDR Gold Shares',
    logoUrl: 'https://icons.duckduckgo.com/ip3/spdrgoldshares.com.ico',
    score: 63,
    price: '$308.72',
    change: '+0.91%',
    direction: 'Defensive',
    read: 'Worth watching as the hedge trade if higher yields stop mattering or risk appetite cracks.',
    chart: [30, 29, 31, 30, 34, 33, 36, 35, 39, 38, 42, 41],
    factors: [
      { label: 'Earnings', read: 'No company earnings driver; this is a macro instrument.' },
      { label: 'Guidance', read: 'Guidance proxy is real yields, dollar, and central-bank tone.' },
      { label: 'Analysts', read: 'Less useful intraday than rates and DXY confirmation.' },
      { label: 'Macro sensitivity', read: 'Works best if real yields soften or risk-off demand appears.' },
      { label: 'Sector sympathy', read: 'Watch miners and silver for confirmation.' },
      { label: 'Unusual volume', read: 'Tradable liquidity, but wait for a clean range break.' },
      { label: 'News catalyst', read: 'Fed path and inflation interpretation drive the setup.' },
      { label: 'Social attention', read: 'Not the loudest WSB name, but useful as a defensive alternative.' },
      { label: 'Fresh or crowded', read: 'Not crowded; edge depends on patience at levels.' },
    ],
  },
];

export default function Home() {
  return (
    <AuthGate>
      <TradeBuilder />
    </AuthGate>
  );
}

function TradeBuilder() {
  useKeyboardInset();

  const [selected, setSelected] = useState<AssetKey | null>(null);
  const [phase, setPhase] = useState<Phase>('brief_work');
  const [bias, setBias] = useState<Side | null>(null);
  const [levels, setLevels] = useState<Levels | null>(null);
  const [locationReady, setLocationReady] = useState(false);
  const [detail, setDetail] = useState<Detail>(null);
  const [briefComplete, setBriefComplete] = useState(false);
  const [tickerStage, setTickerStage] = useState<'brief' | 'exiting' | 'picker'>('brief');

  const asset = selected ? ASSETS[selected] : null;
  const plan = asset && bias ? (bias === 'bullish' ? asset.bull : asset.bear) : null;
  const activeRun = useMemo<ActiveRun | null>(() => {
    if (phase === 'brief_work') {
      return {
        key: 'daily-brief',
        status: 'building daily brief',
        title: "Preparing today's macro color.",
        lines: [
          'read_macro_calendar(date="2026-07-31")',
          'check_earnings_reaction(["AMZN", "AAPL"])',
          'watch_yields_dollar_oil()',
          'summarize_daily_macro_color()',
        ],
        sources: MARKET_BRIEF.sources,
        conclusion: 'Daily macro color is ready. Start with the backdrop, then pick one trade.',
        nextPhase: 'brief',
        durationMs: 5000,
        minDurationMs: 5000,
      };
    }

    if (!asset) return null;

    if (phase === 'preflight') {
      return {
        key: `preflight-${asset.ticker}`,
        status: 'scanning market',
        title: `Building the first read on ${asset.ticker}.`,
        lines: [
          `load_quote('${asset.ticker}')`,
          'rank_tradeability()',
          'scan_news_catalysts()',
          'stress_test_headline_risk()',
        ],
        sources: asset.sources,
        conclusion: asset.agentRead,
        nextPhase: 'catalyst',
      };
    }

    if (phase === 'macro_work') {
      return {
        key: `macro-${asset.ticker}`,
        status: 'checking regime',
        title: 'Checking whether the backdrop helps or fights us.',
        lines: [
          'read_rates_and_dollar()',
          'check_index_breadth()',
          'flag_next_macro_event()',
          'apply_regime_filter()',
        ],
        sources: ['Macro board', 'Breadth', 'Event calendar'],
        conclusion: asset.macroVerdict,
        nextPhase: 'macro',
      };
    }

    if (phase === 'technical_work' && bias) {
      return {
        key: `technical-${asset.ticker}-${bias}`,
        status: 'checking trade location',
        title: 'Checking whether this entry is late.',
        lines: [
          'load_intraday_candles()',
          'map_support_resistance()',
          'measure_distance_from_invalidation()',
          'check_if_chasing_supply()',
          'confirm_volume_expansion()',
        ],
        sources: ['Candlesticks', 'Support/resistance', 'Volume', 'Range extension'],
        conclusion: 'Location read is ready. Decide if the entry is clean enough to keep building.',
        nextPhase: 'technical',
      };
    }

    if (phase === 'setup_work' && bias) {
      return {
        key: `setup-${asset.ticker}-${bias}`,
        status: 'building levels',
        title: 'Turning the thesis into a risk-defined setup.',
        lines: [
          `entry = anchor_near_trigger('${asset.ticker}')`,
          'stop = place_at_invalidation()',
          'target = front_run_resistance()',
          'rr = reward / risk',
        ],
        sources: ['Trigger', 'Invalidation', 'R:R model'],
        conclusion: 'Levels are ready. Your job is to move them until the risk feels right.',
        nextPhase: 'levels',
      };
    }

    return null;
  }, [asset, bias, phase]);

  const metrics = useMemo(() => {
    if (!levels || !bias) return null;
    const risk = bias === 'bullish' ? levels.entry - levels.stop : levels.stop - levels.entry;
    const reward = bias === 'bullish' ? levels.target - levels.entry : levels.entry - levels.target;
    const valid = risk > 0 && reward > 0;
    return { risk, reward, rr: valid ? reward / risk : 0, valid };
  }, [bias, levels]);

  const agentStatus = activeRun?.status ?? statusForPhase(phase);

  function reset() {
    setSelected(null);
    setPhase('brief_work');
    setBias(null);
    setLevels(null);
    setLocationReady(false);
    setDetail(null);
    setBriefComplete(false);
    setTickerStage('brief');
  }

  function selectAsset(ticker: AssetKey) {
    setSelected(ticker);
    setPhase('direction');
    setBias(null);
    setLevels(null);
    setLocationReady(false);
    setDetail(null);
    setTickerStage('brief');
  }

  function openTickerFlow() {
    if (!asset && (phase !== 'brief' || !briefComplete)) return;
    if (!asset && phase === 'brief' && briefComplete) {
      if (tickerStage === 'brief') {
        setTickerStage('exiting');
        window.setTimeout(() => {
          setTickerStage('picker');
        }, 650);
        return;
      }
      return;
    }
  }

  function chooseBias(nextBias: Bias) {
    setDetail(null);
    if (nextBias === 'no_trade') {
      setBias(null);
      setLevels(null);
      setPhase('final');
      return;
    }
    const nextPlan = asset![nextBias === 'bullish' ? 'bull' : 'bear'];
    setBias(nextBias);
    setLocationReady(false);
    setLevels({ entry: nextPlan.entry, target: nextPlan.target, stop: nextPlan.stop });
    setPhase('technical');
  }

  return (
    <main className="min-h-dvh bg-[#070706] text-[#f5f1e8]">
      <TopBar
        status={agentStatus}
        onReset={reset}
        showReset={selected !== null}
        run={activeRun}
        onRunDone={() => {
          if (activeRun) setPhase(activeRun.nextPhase);
        }}
      />
      <div className="relative mx-auto flex min-h-dvh w-full max-w-[560px] flex-col px-4 pb-[120px] pt-2 sm:px-6">
        <section className="flex flex-1 flex-col justify-start pb-6 pt-5">
          {!asset && phase === 'brief' && (
            <div className="relative min-h-[calc(100dvh-170px)]">
              {tickerStage !== 'picker' && (
                <div className={tickerStage === 'exiting' ? 'brief-swipe-out' : undefined}>
                  <DailyBriefPanel onComplete={() => setBriefComplete(true)} />
                </div>
              )}
              {tickerStage === 'picker' && <TickerSelectionScreen onPick={selectAsset} />}
            </div>
          )}

          {asset && phase === 'catalyst' && (
            <CatalystPanel
              asset={asset}
              detail={detail}
              onDetail={setDetail}
              onContinue={() => {
                setDetail(null);
                setPhase('macro_work');
              }}
              onPass={() => setPhase('final')}
            />
          )}

          {asset && phase === 'macro' && (
            <MacroPanel
              asset={asset}
              onContinue={() => setPhase('direction')}
              onPass={() => setPhase('final')}
            />
          )}

          {asset && phase === 'direction' && (
            <DirectionPanel asset={asset} />
          )}

          {asset && plan && phase === 'technical' && (
            <TechnicalPanel
              asset={asset}
              side={bias!}
              plan={plan}
              onAnalysisComplete={() => setLocationReady(true)}
            />
          )}

          {asset && plan && levels && metrics && phase === 'levels' && (
            <LevelsPanel
              asset={asset}
              side={bias!}
              plan={plan}
              levels={levels}
              metrics={metrics}
              detail={detail}
              onDetail={setDetail}
              onLevelsChange={setLevels}
              onSave={() => {
                setDetail(null);
                setPhase('final');
              }}
            />
          )}

          {asset && phase === 'final' && (
            <FinalPanel asset={asset} side={bias} levels={levels} metrics={metrics} plan={plan} onRestart={reset} />
          )}
        </section>
      </div>

      <TickerSelectBar
        selected={asset}
        phase={phase}
        briefComplete={briefComplete}
        tickerStage={tickerStage}
        onOpen={openTickerFlow}
        onChooseBias={chooseBias}
        locationReady={locationReady}
      />
    </main>
  );
}

function useKeyboardInset() {
  useEffect(() => {
    function updateInset() {
      const viewport = window.visualViewport;
      const inset = viewport ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0;
      document.documentElement.style.setProperty('--keyboard-inset', `${Math.round(inset)}px`);
    }

    updateInset();
    window.visualViewport?.addEventListener('resize', updateInset);
    window.visualViewport?.addEventListener('scroll', updateInset);
    window.addEventListener('orientationchange', updateInset);

    return () => {
      window.visualViewport?.removeEventListener('resize', updateInset);
      window.visualViewport?.removeEventListener('scroll', updateInset);
      window.removeEventListener('orientationchange', updateInset);
      document.documentElement.style.removeProperty('--keyboard-inset');
    };
  }, []);
}

function TopBar({
  status,
  onReset,
  showReset,
  run,
  onRunDone,
}: {
  status: string;
  onReset: () => void;
  showReset: boolean;
  run: ActiveRun | null;
  onRunDone: () => void;
}) {
  return (
    <header className="sticky top-0 z-40 bg-[#070706]">
      <TerminalStrip label={status} />
      {showReset && (
        <button
          type="button"
          onClick={onReset}
          className="absolute right-3 top-1 border border-white/10 bg-white/[0.06] px-2 py-0.5 text-[10px] font-semibold text-[#b9b1a4] active:scale-95"
        >
          New
        </button>
      )}
      {run && <TopAgentDrawer key={run.key} run={run} onDone={onRunDone} />}
    </header>
  );
}

function DailyBriefPanel({ onComplete }: { onComplete: () => void }) {
  const [stage, setStage] = useState<'label' | 'title' | 'bullets' | 'factors'>('label');

  return (
    <section className="animate-slide-in">
      <div>
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[#4a96ff]">
          <TypedText text="Macro backdrop" speedMs={46} onDone={() => setStage('title')} />
        </div>
        {stage !== 'label' && (
          <h1 className="mt-2 text-[32px] font-semibold leading-tight tracking-normal text-[#f7f1e6]">
            <TypedHeadline
              prefix="Today's brief: "
              highlight="Bullish"
              speedMs={38}
              startDelayMs={220}
              onDone={() => setStage('bullets')}
            />
          </h1>
        )}
      </div>

      {stage !== 'label' && stage !== 'title' && (
        <TypedBriefBullets items={MARKET_BRIEF.bullets} onDone={() => setStage('factors')} />
      )}
      {stage === 'factors' && <DailyBriefFactors onDone={onComplete} />}
    </section>
  );
}

function DailyBriefFactors({ onDone }: { onDone: () => void }) {
  useEffect(() => {
    onDone();
  }, [onDone]);

  return (
    <section className="mt-7 animate-slide-in border-t border-white/10 pt-4">
      <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-[#4a96ff]">Regime checklist</div>
      <div className="grid gap-2">
        {DAILY_BRIEF_FACTORS.map((factor) => (
          <div key={factor.label} className="rounded-2xl border border-white/[0.06] bg-white/[0.035] px-3 py-2.5">
            <div className="text-[12px] font-semibold text-[#f7f1e6]">{factor.label}</div>
            <div className="mt-1 min-h-[34px] text-[12.5px] leading-snug text-[#9f978b]">{factor.read}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function TickerSelectionScreen({ onPick }: { onPick: (ticker: AssetKey) => void }) {
  const [ready, setReady] = useState(false);

  return (
    <section className="animate-slide-in pt-2">
      <h1 className="whitespace-nowrap text-[24px] font-semibold uppercase leading-tight tracking-normal text-[#f7f1e6] sm:text-[34px]">
        <TypedText
          text="Trending Trades"
          speedMs={46}
          onDone={() => setReady(true)}
        />
      </h1>
      {ready && <TickerIdeaSwiper ideas={TICKER_IDEAS} onPick={onPick} />}
    </section>
  );
}

function TickerIdeaSwiper({ ideas, onPick }: { ideas: TickerIdea[]; onPick: (ticker: AssetKey) => void }) {
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActiveIndex((index) => (index + 1) % ideas.length);
    }, 5000);

    return () => window.clearInterval(timer);
  }, [ideas.length]);

  return (
    <div className="mt-6 overflow-hidden" aria-label="Trade ideas">
      <div
        className="flex transition-transform duration-700 ease-[cubic-bezier(0.2,0.8,0.2,1)]"
        style={{ transform: `translateX(-${activeIndex * 100}%)` }}
      >
        {ideas.map((idea) => (
          <div key={idea.ticker} className="w-full shrink-0">
            <TickerIdeaCard idea={idea} onPick={() => onPick(idea.ticker)} />
          </div>
        ))}
      </div>
      <div className="mt-3 flex justify-center gap-1.5">
        {ideas.map((idea, index) => (
          <button
            key={idea.ticker}
            type="button"
            aria-label={`Show ${idea.ticker}`}
            onClick={() => setActiveIndex(index)}
            className={`h-1.5 rounded-full transition-all ${index === activeIndex ? 'w-5 bg-[#4a96ff]' : 'w-1.5 bg-white/20'}`}
          />
        ))}
      </div>
    </div>
  );
}

function TickerIdeaCard({ idea, onPick }: { idea: TickerIdea; onPick: () => void }) {
  const thesis = thesisBreakdown(ASSETS[idea.ticker], idea);
  const overview = thesis.find((section) => section.label === 'One line overview')?.text ?? idea.read;
  const catalyst = thesis.find((section) => section.label === 'Catalyst')?.text ?? idea.read;
  const risks = thesis.find((section) => section.label === 'Key risks')?.text ?? ASSETS[idea.ticker].mainRisk;

  return (
    <button
      type="button"
      onClick={onPick}
      className="w-full rounded-[22px] border border-[#34312e] bg-[#12110f] p-4 text-left shadow-2xl shadow-black/35 active:scale-[0.99]"
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <TickerLogo ticker={idea.ticker} name={idea.name} logoUrl={idea.logoUrl} />
          <div className="min-w-0">
            <div className="font-mono text-[22px] font-semibold leading-none text-[#f7f1e6]">${idea.ticker}</div>
            <div className="mt-1 truncate text-[11px] font-medium text-[#8c8578]">{idea.name}</div>
          </div>
        </div>
      </div>

      <MiniIdeaChart points={idea.chart} />

      <div className="mt-3 flex items-center justify-between gap-3 border-b border-white/10 pb-3">
        <div className="font-mono text-[15px] font-semibold text-[#f7f1e6]">{idea.price}</div>
        <div className="rounded-full bg-[#102018] px-2 py-1 font-mono text-[11px] font-semibold text-[#29b987]">{idea.change}</div>
      </div>

      <div className="mt-3 text-[13px] leading-snug text-[#c9c0b2]">{overview}</div>

      <div className="mt-3 grid gap-2">
        <div className="border-t border-white/10 pt-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#4a96ff]">Key catalyst</div>
          <p className="mt-1.5 text-[12px] leading-snug text-[#9f978b]">{catalyst}</p>
        </div>
        <div className="border-t border-white/10 pt-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#4a96ff]">Key risks</div>
          <p className="mt-1.5 text-[12px] leading-snug text-[#9f978b]">{risks}</p>
        </div>
      </div>

      <span className="mt-4 flex h-11 w-full items-center justify-center rounded-full bg-[#4a96ff] text-[14px] font-semibold text-[#07111f] shadow-lg shadow-[#4a96ff]/20">
        Build Trade
      </span>
    </button>
  );
}

function MiniIdeaChart({ points }: { points: number[] }) {
  const width = 270;
  const height = 82;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const spread = Math.max(1, max - min);
  const scaled = points.map((point, index) => ({
    x: (index / Math.max(1, points.length - 1)) * width,
    y: height - ((point - min) / spread) * (height - 14) - 7,
  }));
  const path = points
    .map((_, index) => {
      const { x, y } = scaled[index] ?? { x: 0, y: height / 2 };
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(' ');
  const lastPoint = scaled[scaled.length - 1] ?? { x: width, y: height / 2 };

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-[82px] w-full rounded-2xl bg-[#0b0a09]" role="img" aria-label="Intraday chart preview">
      <path d="M 0 67 H 270" stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
      <path d="M 0 41 H 270" stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
      <path d="M 0 15 H 270" stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
      <path d={path} fill="none" stroke="#4a96ff" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" />
      <circle cx={lastPoint.x} cy={lastPoint.y} r="3.5" fill="#4a96ff" />
    </svg>
  );
}

function MacroFactorMarquee({ compact = false }: { compact?: boolean }) {
  const [factors, setFactors] = useState<LiveMacroFactor[]>(
    () => MACRO_FACTORS.map((factor) => ({ ...factor, points: [...factor.points], flash: null })),
  );
  const track = [...factors, ...factors];

  useEffect(() => {
    const timer = window.setInterval(() => {
      const targetIndex = Math.floor(Math.random() * MACRO_FACTORS.length);
      setFactors((current) =>
        current.map((factor, index) => {
          if (index !== targetIndex) return { ...factor, flash: null };

          const unit = factor.value > 1000 ? 5 : factor.value > 50 ? 0.04 : 0.02;
          const direction = Math.random() > 0.45 ? 1 : -1;
          const nextValue = Math.max(0.01, factor.value + direction * unit * (0.55 + Math.random()));
          const lastPoint = factor.points[factor.points.length - 1] ?? 32;
          const nextPoint = clamp(lastPoint + direction * (2 + Math.random() * 6), 10, 50);

          return {
            ...factor,
            value: nextValue,
            flash: direction > 0 ? 'up' : 'down',
            points: [...factor.points.slice(1), nextPoint],
          };
        }),
      );
    }, 900);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="overflow-hidden" aria-label="Live macro factor tape">
      <div className={`flex w-max animate-macro-marquee ${compact ? 'gap-1.5' : 'gap-2'}`}>
        {track.map((factor, index) => (
          <MacroFactorTile key={`${factor.label}-${index}`} factor={factor} compact={compact} />
        ))}
      </div>
    </div>
  );
}

function CatalystPanel({
  asset,
  detail,
  onDetail,
  onContinue,
  onPass,
}: {
  asset: DemoAsset;
  detail: Detail;
  onDetail: (detail: Detail) => void;
  onContinue: () => void;
  onPass: () => void;
}) {
  const idea = TICKER_IDEAS.find((item) => item.ticker === asset.ticker);
  const thesis = thesisBreakdown(asset, idea);

  return (
    <FocusCard label="Thesis" title="What's the trade?">
      <div className="mb-4 flex items-center justify-between gap-3 border-b border-white/10 pb-4">
        <div className="min-w-0">
          <div className="font-mono text-[18px] font-semibold text-[#f7f1e6]">${asset.ticker}</div>
          <div className="mt-1 truncate text-[12px] font-medium text-[#8c8578]">{asset.name}</div>
        </div>
      </div>

      <TypedThesisSections sections={thesis} />

      <DetailToggles detail={detail} onDetail={onDetail} enabled={['sources']} />
      {detail === 'sources' && <DetailPanel items={asset.sources} />}
      <PrimaryAction onClick={onContinue}>Check macro</PrimaryAction>
      <SecondaryAction onClick={onPass}>Pass for now</SecondaryAction>
    </FocusCard>
  );
}

function thesisBreakdown(asset: DemoAsset, idea?: TickerIdea): ThesisSection[] {
  if (asset.ticker === 'MU') {
    return [
      {
        label: 'One line overview',
        text: 'MU is the cleanest momentum idea if AI-memory demand keeps pulling buyers into semis.',
      },
      {
        label: 'Catalyst',
        text: 'The catalyst is still HBM demand, memory pricing, and AI-server capex. Earnings and guidance keep the story alive, analyst tone is constructive, and retail attention is elevated, so the trade has real energy today.',
      },
      {
        label: 'Key risks',
        text: 'The risk is crowding. If semis stop leading, volume fades, or MU loses the breakout area, the long can turn into a trapped momentum trade quickly.',
      },
    ];
  }

  if (asset.ticker === 'SPY') {
    return [
      {
        label: 'One line overview',
        text: 'SPY is the cleanest broad-market trade if buyers keep defending shallow pullbacks.',
      },
      {
        label: 'Catalyst',
        text: 'The catalyst is market regime. The index is absorbing Fed, inflation, and earnings news without breaking down, while mega-cap tech leadership keeps the tape constructive.',
      },
      {
        label: 'Key risks',
        text: 'The risk is narrow leadership. If breadth weakens, rates push higher, or mega-cap tech stops carrying the tape, SPY can lose support faster than the calm volatility suggests.',
      },
    ];
  }

  if (asset.ticker === 'GLD') {
    return [
      {
        label: 'One line overview',
        text: 'GLD is the defensive setup if the market starts paying for protection instead of beta.',
      },
      {
        label: 'Catalyst',
        text: 'The catalyst is macro, not earnings: real yields, the dollar, Fed tone, and inflation interpretation. GLD gets more interesting if yields soften or risk appetite starts to crack.',
      },
      {
        label: 'Key risks',
        text: 'The risk is a firm dollar plus firm real yields. If both stay elevated, gold can chop inside the range and punish breakout entries.',
      },
    ];
  }

  return [
    { label: 'One line overview', text: idea?.read ?? asset.catalyst },
    { label: 'Catalyst', text: asset.catalyst },
    { label: 'Key risks', text: asset.catalystRisk },
  ];
}

function TypedThesisSections({ sections }: { sections: ThesisSection[] }) {
  const seed = sections.map((section) => `${section.label}:${section.text}`).join('\n');
  const [sectionIndex, setSectionIndex] = useState(0);
  const [charIndex, setCharIndex] = useState(0);

  useEffect(() => {
    setSectionIndex(0);
    setCharIndex(0);
  }, [seed]);

  useEffect(() => {
    if (sectionIndex >= sections.length) return;
    const current = sections[sectionIndex]?.text ?? '';
    if (charIndex < current.length) {
      const timer = window.setTimeout(() => setCharIndex((idx) => idx + 1), 22);
      return () => window.clearTimeout(timer);
    }
    const timer = window.setTimeout(() => {
      setSectionIndex((idx) => idx + 1);
      setCharIndex(0);
    }, 260);
    return () => window.clearTimeout(timer);
  }, [charIndex, sectionIndex, sections]);

  const visibleSections = sections.slice(0, Math.min(sections.length, sectionIndex + 1));

  return (
    <div className="mb-4 grid gap-2">
      {visibleSections.map((section, index) => {
        const visible = index < sectionIndex ? section.text : section.text.slice(0, charIndex);
        return (
          <section key={section.label} className="rounded-2xl border border-white/[0.06] bg-white/[0.035] px-3 py-3">
            <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#4a96ff]">{section.label}</div>
            <div className="mt-2 text-[13px] leading-snug text-[#c9c0b2]">
              {visible}
              {index === sectionIndex && charIndex < section.text.length && (
                <span className="ml-px inline-block h-3 w-px bg-current align-middle animate-caret-blink" />
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function MacroPanel({ asset, onContinue, onPass }: { asset: DemoAsset; onContinue: () => void; onPass: () => void }) {
  return (
    <FocusCard label="Regime" title="Is the backdrop helping or fighting us?">
      <TypedBullets items={[`Macro read: ${asset.macroRead}`, `Next event: ${asset.macroEvent}`, asset.macroVerdict]} />
      <div className="mt-3 rounded-2xl border border-[#6b5cff]/30 bg-[#191733] px-3 py-2.5 text-[13px] font-semibold text-[#bdb7ff]">
        {asset.macroVerdict}
      </div>
      <PrimaryAction onClick={onContinue}>Choose direction</PrimaryAction>
      <SecondaryAction onClick={onPass}>No trade</SecondaryAction>
    </FocusCard>
  );
}

function DirectionPanel({
  asset,
}: {
  asset: DemoAsset;
}) {
  const bullWeight = asset.recommendedBias === 'bullish' ? 64 : 42;
  const bearWeight = 100 - bullWeight;
  const idea = TICKER_IDEAS.find((item) => item.ticker === asset.ticker);

  return (
    <section className="animate-slide-in px-1 py-2">
      <DirectionPriceChart asset={asset} points={idea?.chart ?? []} bullWeight={bullWeight} bearWeight={bearWeight} />

      <div className="mt-5 grid gap-4">
        <section className="border-t border-white/10 pt-4">
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-[#29b987]">Bull case</div>
          <p className="text-[14px] leading-relaxed text-[#d5cbbd]">{directionBullParagraph(asset)}</p>
        </section>

        <section className="border-t border-white/10 pt-4">
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-[#ef5350]">Bear case</div>
          <p className="text-[14px] leading-relaxed text-[#d5cbbd]">{directionBearParagraph(asset)}</p>
        </section>

      </div>
    </section>
  );
}

function DirectionPriceChart({
  asset,
  points,
  bullWeight,
  bearWeight,
}: {
  asset: DemoAsset;
  points: number[];
  bullWeight: number;
  bearWeight: number;
}) {
  const chartPoints = points.length > 1 ? points : [42, 45, 43, 47, 51, 49, 54, 52, 57, 55, 59, 62];
  const width = 390;
  const height = 132;
  const currentPoint = { x: 160, y: height / 2 };
  const min = Math.min(...chartPoints);
  const max = Math.max(...chartPoints);
  const spread = Math.max(1, max - min);
  const scaled = chartPoints.map((point, index) => ({
    x: 8 + (index / Math.max(1, chartPoints.length - 1)) * (currentPoint.x - 18),
    y: currentPoint.y + 24 - ((point - min) / spread) * 48,
  }));
  const historyPath = [...scaled, currentPoint]
    .map(({ x, y }, index) => `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(' ');
  const bullishPaths = [
    [
      currentPoint,
      { x: 182, y: 56 },
      { x: 205, y: 60 },
      { x: 229, y: 43 },
      { x: 251, y: 46 },
      { x: 278, y: 29 },
      { x: 304, y: 34 },
    ],
    [
      currentPoint,
      { x: 184, y: 61 },
      { x: 207, y: 51 },
      { x: 235, y: 55 },
      { x: 259, y: 38 },
      { x: 286, y: 42 },
      { x: 308, y: 24 },
    ],
    [
      currentPoint,
      { x: 181, y: 63 },
      { x: 211, y: 66 },
      { x: 235, y: 52 },
      { x: 260, y: 57 },
      { x: 283, y: 47 },
      { x: 306, y: 50 },
    ],
  ];
  const bearishPaths = [
    [
      currentPoint,
      { x: 183, y: 75 },
      { x: 207, y: 72 },
      { x: 230, y: 88 },
      { x: 252, y: 84 },
      { x: 280, y: 106 },
      { x: 304, y: 101 },
    ],
    [
      currentPoint,
      { x: 182, y: 70 },
      { x: 206, y: 82 },
      { x: 232, y: 78 },
      { x: 258, y: 97 },
      { x: 282, y: 94 },
      { x: 307, y: 116 },
    ],
    [
      currentPoint,
      { x: 184, y: 78 },
      { x: 211, y: 91 },
      { x: 236, y: 87 },
      { x: 259, y: 101 },
      { x: 283, y: 103 },
      { x: 305, y: 112 },
    ],
  ];
  const toPath = (pathPoints: Array<{ x: number; y: number }>) =>
    pathPoints
      .map(({ x, y }, index) => `${index === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`)
      .join(' ');

  return (
    <div className="mb-5">
      <div className="mb-2 flex items-end justify-between gap-3">
        <div>
          <div className="font-mono text-[18px] font-semibold leading-none text-[#f7f1e6]">${asset.ticker}</div>
          <div className="mt-1 truncate text-[11px] font-medium text-[#8c8578]">{asset.name}</div>
        </div>
        <div className="text-right">
          <div className="font-mono text-[18px] font-semibold leading-none text-[#f7f1e6]">${asset.last.toFixed(2)}</div>
          <div className={asset.changePct >= 0 ? 'mt-1 font-mono text-[11px] text-[#29b987]' : 'mt-1 font-mono text-[11px] text-[#ef5350]'}>
            {asset.changePct >= 0 ? '+' : ''}
            {asset.changePct.toFixed(2)}%
          </div>
        </div>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-[132px] w-full" role="img" aria-label={`${asset.ticker} forward price path chart`}>
        <defs>
          <marker id="bull-path-arrow" markerWidth="5" markerHeight="5" refX="4.5" refY="2.5" orient="auto">
            <path d="M 0 0 L 5 2.5 L 0 5 z" fill="#1f7a4d" opacity="0.72" />
          </marker>
          <marker id="bear-path-arrow" markerWidth="5" markerHeight="5" refX="4.5" refY="2.5" orient="auto">
            <path d="M 0 0 L 5 2.5 L 0 5 z" fill="#8f2b2b" opacity="0.72" />
          </marker>
        </defs>
        <path d="M 0 28 H 390" stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
        <path d="M 0 66 H 390" stroke="rgba(255,255,255,0.09)" strokeWidth="1" />
        <path d="M 0 104 H 390" stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
        <path d={historyPath} fill="none" stroke="#4a96ff" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.7" />
        {bullishPaths.map((pathPoints, index) => (
          <path
            key={`bull-${index}`}
            d={toPath(pathPoints)}
            fill="none"
            stroke="#1f7a4d"
            strokeDasharray={index === 0 ? '5 6' : '3 8'}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeOpacity={index === 0 ? 0.82 : 0.24}
            strokeWidth={index === 0 ? 2.7 : 1.5}
            markerEnd={index === 0 ? 'url(#bull-path-arrow)' : undefined}
          />
        ))}
        {bearishPaths.map((pathPoints, index) => (
          <path
            key={`bear-${index}`}
            d={toPath(pathPoints)}
            fill="none"
            stroke="#8f2b2b"
            strokeDasharray={index === 0 ? '5 6' : '3 8'}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeOpacity={index === 0 ? 0.82 : 0.24}
            strokeWidth={index === 0 ? 2.7 : 1.5}
            markerEnd={index === 0 ? 'url(#bear-path-arrow)' : undefined}
          />
        ))}
        <circle className="chart-branch-pulse" cx={currentPoint.x} cy={currentPoint.y} r="7.5" fill="#4a96ff" />
        <circle cx={currentPoint.x} cy={currentPoint.y} r="3.4" fill="#4a96ff" />
        <text x="322" y="34" fill="#29b987" fontSize="12" fontFamily="monospace" fontWeight="700" textAnchor="start">
          {bullWeight}% up
        </text>
        <text x="322" y="104" fill="#ef5350" fontSize="12" fontFamily="monospace" fontWeight="700" textAnchor="start">
          {bearWeight}% down
        </text>
      </svg>
    </div>
  );
}

function ProbabilityRead({
  asset,
  bullWeight,
  bearWeight,
}: {
  asset: DemoAsset;
  bullWeight: number;
  bearWeight: number;
}) {
  const favored = bullWeight >= bearWeight ? 'upside' : 'downside';
  const favoredPct = Math.max(bullWeight, bearWeight);
  const opposingPct = Math.min(bullWeight, bearWeight);

  return (
    <div className="mb-4 border-y border-white/10 py-3">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-[#4a96ff]">Probability read</div>
      <p className="text-[13px] leading-relaxed text-[#d5cbbd]">
        My read gives {favored} the cleaner setup path at roughly {favoredPct}% versus {opposingPct}% the other way.
        For ${asset.ticker}, that comes from catalyst direction, relative strength, price reaction, and whether buyers
        are defending the first important level instead of letting the move fade.
      </p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <div>
          <div className="mb-1 flex items-center justify-between font-mono text-[11px] text-[#29b987]">
            <span>UP</span>
            <span>{bullWeight}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
            <div className="h-full bg-[#1f7a4d]" style={{ width: `${bullWeight}%` }} />
          </div>
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between font-mono text-[11px] text-[#ef5350]">
            <span>DOWN</span>
            <span>{bearWeight}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
            <div className="h-full bg-[#7f2626]" style={{ width: `${bearWeight}%` }} />
          </div>
        </div>
      </div>
    </div>
  );
}

function DirectionalBiasBar({
  bullWeight,
  bearWeight,
}: {
  bullWeight: number;
  bearWeight: number;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-[10px] font-semibold uppercase tracking-[0.14em]">
        <span className="text-[#29b987]">Bullish</span>
        <span className="text-[#ef5350]">Bearish</span>
      </div>
      <div className="flex h-2 overflow-hidden rounded-full bg-white/10" aria-label="Bull bear balance">
        <div className="h-full bg-[#1f7a4d]" style={{ width: `${bullWeight}%` }} />
        <div className="h-full bg-[#7f2626]" style={{ width: `${bearWeight}%` }} />
      </div>
    </div>
  );
}

function directionBullParagraph(asset: DemoAsset) {
  const support = asset.supports[0];
  return `${asset.bullSummary} The constructive read is that the catalyst still points in the right direction, the broader regime has not rejected risk, and the ticker has enough relative strength to keep buyers engaged. I would only respect the long if price holds the key support area near $${support.toFixed(2)} and volume confirms instead of fading after the first push.`;
}

function directionBearParagraph(asset: DemoAsset) {
  const support = asset.supports[0];
  return `${asset.bearSummary} The bear case matters if the market starts selling the news, sector leadership weakens, or the move becomes crowded without fresh volume behind it. If ${asset.ticker} loses $${support.toFixed(2)} while SPY or QQQ stay heavy, I would treat that as a warning that the better decision may be bearish or no trade.`;
}

function TechnicalPanel({
  asset,
  side,
  plan,
  onAnalysisComplete,
}: {
  asset: DemoAsset;
  side: Side;
  plan: SetupPlan;
  onAnalysisComplete: () => void;
}) {
  const read = tradeLocationRead(asset, side, plan);
  const sections = locationAnalysisSections(asset, side, plan, read);
  const [analysisStep, setAnalysisStep] = useState(0);
  const notifiedRef = useRef(false);

  useEffect(() => {
    setAnalysisStep(0);
    notifiedRef.current = false;
    const timers = [700, 2200, 3800, 5400].map((delay, index) =>
      window.setTimeout(() => setAnalysisStep(index + 1), delay),
    );
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [asset.ticker, side]);

  useEffect(() => {
    if (analysisStep < 4 || notifiedRef.current) return;
    notifiedRef.current = true;
    onAnalysisComplete();
  }, [analysisStep, onAnalysisComplete]);

  return (
    <section className="animate-slide-in px-1 py-2">
      <LocationCandlestickChart asset={asset} side={side} plan={plan} analysisStep={analysisStep} />

      <div className="mt-5 grid gap-4">
        {sections.slice(0, analysisStep).map((section) => (
          <TypedLocationSection key={`${asset.ticker}-${side}-${section.label}`} label={section.label} text={section.text} />
        ))}
      </div>

      {analysisStep >= 4 && <TradeSetupPopup plan={plan} />}
    </section>
  );
}

function LocationCandlestickChart({
  asset,
  side,
  plan,
  analysisStep,
}: {
  asset: DemoAsset;
  side: Side;
  plan: SetupPlan;
  analysisStep: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const candles = useMemo(() => buildLocationCandles(asset, side), [asset, side]);
  const chartHeight = analysisStep >= 3 ? 430 : analysisStep >= 2 ? 350 : 250;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      width: el.clientWidth,
      height: chartHeight,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#8c8578',
        attributionLogo: false,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: 'rgba(255,255,255,0.055)' },
      },
      rightPriceScale: {
        borderVisible: false,
        textColor: '#8c8578',
      },
      timeScale: {
        borderVisible: false,
        timeVisible: false,
        secondsVisible: false,
      },
      crosshair: {
        horzLine: { color: 'rgba(255,255,255,0.14)' },
        vertLine: { color: 'rgba(255,255,255,0.14)' },
      },
      handleScroll: false,
      handleScale: false,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#1f7a4d',
      downColor: '#8f2b2b',
      wickUpColor: '#29b987',
      wickDownColor: '#ef5350',
      borderVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    candleSeries.setData(candles);

    if (analysisStep >= 1) {
      for (const resistance of asset.resistances.slice(0, 2)) {
        candleSeries.createPriceLine({
          price: resistance,
          color: '#8f2b2b',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'R',
        });
      }
      for (const support of asset.supports.slice(0, 2)) {
        candleSeries.createPriceLine({
          price: support,
          color: '#1f7a4d',
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: 'S',
        });
      }
    }

    if (analysisStep >= 2) {
      const rsiSeries = chart.addSeries(LineSeries, {
        color: '#4a96ff',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      }, 1);
      rsiSeries.setData(buildDemoRsi(candles, side));
      rsiSeries.createPriceLine({ price: 70, color: 'rgba(255,255,255,0.14)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '70' });
      rsiSeries.createPriceLine({ price: 30, color: 'rgba(255,255,255,0.14)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: '30' });
    }

    if (analysisStep >= 3) {
      const volumeSeries = chart.addSeries(HistogramSeries, {
        color: 'rgba(74,150,255,0.42)',
        priceFormat: { type: 'volume' },
        priceLineVisible: false,
        lastValueVisible: false,
      }, 2);
      volumeSeries.setData(
        candles.map((candle) => ({
          time: candle.time,
          value: candle.volume,
          color: candle.close >= candle.open ? 'rgba(31,122,77,0.48)' : 'rgba(143,43,43,0.48)',
        })),
      );
    }

    if (analysisStep >= 2) {
      try {
        const panes = chart.panes();
        if (panes.length > 1) panes[1].setHeight(82);
        if (panes.length > 2) panes[2].setHeight(74);
      } catch {
        // Older pane APIs are non-fatal; the chart still renders in one pane.
      }
    }

    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [analysisStep, asset.resistances, asset.supports, asset.ticker, candles, chartHeight, side]);

  return (
    <div>
      <div className="mb-2 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="font-mono text-[18px] font-semibold leading-none text-[#f7f1e6]">${asset.ticker}</div>
          <div className="mt-1 truncate text-[11px] font-medium text-[#8c8578]">{asset.name}</div>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-[18px] font-semibold leading-none text-[#f7f1e6]">${asset.last.toFixed(2)}</div>
          <div className={asset.changePct >= 0 ? 'mt-1 font-mono text-[11px] text-[#29b987]' : 'mt-1 font-mono text-[11px] text-[#ef5350]'}>
            {asset.changePct >= 0 ? '+' : ''}
            {asset.changePct.toFixed(2)}%
          </div>
        </div>
      </div>
      <div className="relative">
        <div ref={containerRef} className="w-full" style={{ height: chartHeight }} />
        {analysisStep >= 1 && (
          <AnimatedKeyLevelOverlay
            side={side}
            support={asset.supports[0]}
            resistance={asset.resistances[0]}
          />
        )}
        {analysisStep >= 4 && <RiskRewardOverlay side={side} plan={plan} />}
      </div>
    </div>
  );
}

function buildLocationCandles(asset: DemoAsset, side: Side): DemoCandle[] {
  const idea = TICKER_IDEAS.find((item) => item.ticker === asset.ticker);
  const points = idea?.chart ?? [30, 32, 31, 35, 34, 38, 37, 41, 40, 43, 42, 45];
  const min = Math.min(...points);
  const max = Math.max(...points);
  const spread = Math.max(1, max - min);
  const start = asset.last * (side === 'bullish' ? 0.955 : 1.045);
  const end = asset.last;

  return points.map((point, index) => {
    const drift = start + ((point - min) / spread) * (end - start);
    const wave = Math.sin(index * 1.7) * asset.last * 0.004;
    const close = +(drift + wave).toFixed(2);
    const openBase = index === 0 ? close - asset.last * 0.004 : drift - wave * 0.7;
    const open = +openBase.toFixed(2);
    const high = +(Math.max(open, close) + asset.last * (0.006 + (index % 3) * 0.0015)).toFixed(2);
    const low = +(Math.min(open, close) - asset.last * (0.006 + (index % 2) * 0.0012)).toFixed(2);
    return {
      time: `2026-07-${String(15 + index).padStart(2, '0')}`,
      open,
      high,
      low,
      close,
      volume: Math.round(780000 + index * 42000 + Math.abs(close - open) * 120000 + (index % 4) * 85000),
    };
  });
}

function buildDemoRsi(candles: DemoCandle[], side: Side) {
  return candles.map((candle, index) => {
    const base = side === 'bullish' ? 48 + index * 1.75 : 55 - index * 1.55;
    const wave = Math.sin(index * 1.4) * 4.2;
    return {
      time: candle.time,
      value: clamp(+(base + wave).toFixed(2), 24, 76),
    };
  });
}

function AnimatedKeyLevelOverlay({
  support,
  resistance,
}: {
  side: Side;
  support: number;
  resistance: number;
}) {
  return (
    <div className="pointer-events-none absolute left-0 right-12 top-0 h-[250px] overflow-hidden">
      <div className="level-draw absolute left-0 right-0 top-[30%] border-t border-dashed border-[#8f2b2b]/80" />
      <div className="level-label level-label-late absolute right-0 top-[calc(30%-10px)] bg-[#241010] px-1.5 py-0.5 font-mono text-[10px] text-[#ef5350]">
        R ${resistance.toFixed(2)}
      </div>
      <div className="level-draw level-draw-late absolute left-0 right-0 top-[64%] border-t border-dashed border-[#1f7a4d]/80" />
      <div className="level-label level-label-later absolute right-0 top-[calc(64%-10px)] bg-[#102018] px-1.5 py-0.5 font-mono text-[10px] text-[#29b987]">
        S ${support.toFixed(2)}
      </div>
    </div>
  );
}

function RiskRewardOverlay({ side, plan }: { side: Side; plan: SetupPlan }) {
  const rewardTop = side === 'bullish' ? '23%' : '53%';
  const rewardHeight = side === 'bullish' ? '30%' : '22%';
  const riskTop = side === 'bullish' ? '53%' : '31%';
  const riskHeight = side === 'bullish' ? '22%' : '22%';
  const targetTop = side === 'bullish' ? '23%' : '75%';
  const stopTop = side === 'bullish' ? '75%' : '31%';

  return (
    <div className="pointer-events-none absolute left-[42%] right-12 top-0 h-[250px] overflow-hidden">
      <div className="risk-reward-zone absolute left-0 right-0 border border-[#1f7a4d]/50 bg-[#1f7a4d]/18" style={{ top: rewardTop, height: rewardHeight }} />
      <div className="risk-reward-zone risk-zone absolute left-0 right-0 border border-[#8f2b2b]/50 bg-[#8f2b2b]/20" style={{ top: riskTop, height: riskHeight }} />
      <div className="setup-line setup-line-entry absolute left-0 right-0 top-[53%] border-t border-dashed border-[#4a96ff]/90">
        <span className="absolute right-0 top-[-11px] bg-[#0d1828] px-1.5 py-0.5 font-mono text-[10px] text-[#4a96ff]">
          Entry ${plan.entry.toFixed(2)}
        </span>
      </div>
      <div className="setup-line absolute left-0 right-0 border-t border-dashed border-[#1f7a4d]/90" style={{ top: targetTop }}>
        <span className="absolute right-0 top-[-11px] bg-[#102018] px-1.5 py-0.5 font-mono text-[10px] text-[#29b987]">
          Take profit ${plan.target.toFixed(2)}
        </span>
      </div>
      <div className="setup-line absolute left-0 right-0 border-t border-dashed border-[#8f2b2b]/90" style={{ top: stopTop }}>
        <span className="absolute right-0 top-[-11px] bg-[#241010] px-1.5 py-0.5 font-mono text-[10px] text-[#ef5350]">
          Stop loss ${plan.stop.toFixed(2)}
        </span>
      </div>
    </div>
  );
}

function TypedLocationSection({ label, text }: { label: string; text: string }) {
  return (
    <section className="border-t border-white/10 pt-4">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-[#4a96ff]">{label}</div>
      <p className="min-h-[42px] text-[14px] leading-relaxed text-[#d5cbbd]">
        <TypedText text={text} speedMs={18} />
      </p>
    </section>
  );
}

function TradeSetupPopup({ plan }: { plan: SetupPlan }) {
  const [secondsLeft, setSecondsLeft] = useState(13 * 60 + 40);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setSecondsLeft((seconds) => Math.max(0, seconds - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  const minutes = Math.floor(secondsLeft / 60);
  const seconds = String(secondsLeft % 60).padStart(2, '0');

  return (
    <div className="fixed inset-x-0 bottom-0 z-40 bg-[#070706] px-4 pb-4 pt-5">
      <div className="trade-setup-pop mx-auto max-w-[560px] rounded-[22px] border border-[#4a96ff]/70 bg-[#202020] shadow-2xl shadow-black/60">
        <div className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
          <div className="font-mono text-[13px] text-[#9aa0a6]">
            Expires in {minutes}:{seconds}
          </div>
          <button
            type="button"
            className="h-9 rounded-xl border border-white/10 bg-[#242321] px-4 text-[13px] font-semibold text-[#b9b1a4] active:scale-[0.99]"
          >
            Edit
          </button>
        </div>

        <div className="grid grid-cols-3 overflow-hidden border-b border-white/10 text-left">
          <SetupCell label="Entry" value={`$${plan.entry.toFixed(2)}`} tone="blue" />
          <SetupCell label="Take profit" value={`$${plan.target.toFixed(2)}`} tone="green" />
          <SetupCell label="Stop loss" value={`$${plan.stop.toFixed(2)}`} tone="red" />
        </div>

        <div className="px-4 py-3">
          <button
            type="button"
            className="h-12 w-full rounded-2xl bg-[#4a96ff] text-[14px] font-semibold text-[#06111f] shadow-lg shadow-[#4a96ff]/20 active:scale-[0.99]"
          >
            Trade now
          </button>
        </div>
      </div>
    </div>
  );
}

function SetupCell({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  tone?: 'neutral' | 'blue' | 'green' | 'red';
}) {
  const valueClass =
    tone === 'blue'
      ? 'text-[#8fc4ff]'
      : tone === 'green'
        ? 'text-[#78c996]'
        : tone === 'red'
          ? 'text-[#ff8d8d]'
          : 'text-[#f1ede4]';

  return (
    <div className="min-h-[74px] border-r border-b border-white/10 px-3 py-2 last:border-r-0">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#9aa0a6]">{label}</div>
      <div className={`mt-3 text-[15px] font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}

function locationAnalysisSections(
  asset: DemoAsset,
  side: Side,
  plan: SetupPlan,
  read: ReturnType<typeof tradeLocationRead>,
) {
  return [
    {
      label: 'Key levels',
      text: read.levelRead,
    },
    {
      label: 'RSI',
      text:
        side === 'bullish'
          ? `RSI is holding in the upper half of the range without looking exhausted. I want momentum supportive, but not so stretched that we are buying the last push.`
          : `RSI is rolling lower from the middle of the range. I want momentum confirming weakness, but not already washed out into support.`,
    },
    {
      label: 'Volume',
      text: read.volumeRead,
    },
    {
      label: 'Trade location',
      text: `${read.overview} The proposed setup is entry $${plan.entry.toFixed(2)}, target $${plan.target.toFixed(2)}, and stop $${plan.stop.toFixed(2)}.`,
    },
  ];
}

function LocationCheckRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: 'blue' | 'green' | 'amber' | 'red';
}) {
  const toneClass =
    tone === 'green'
      ? 'bg-[#102018] text-[#29b987]'
      : tone === 'red'
        ? 'bg-[#241010] text-[#ef5350]'
        : tone === 'amber'
          ? 'bg-[#251f12] text-[#d6a84f]'
          : 'bg-[#0d1828] text-[#4a96ff]';

  return (
    <div className="border-t border-white/10 pt-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#8c8578]">{label}</div>
        <div className={`shrink-0 rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ${toneClass}`}>
          {tone === 'green' ? 'clean' : tone === 'red' ? 'risk' : tone === 'amber' ? 'watch' : 'check'}
        </div>
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-[#c9c0b2]">{value}</p>
    </div>
  );
}

function tradeLocationRead(asset: DemoAsset, side: Side, plan: SetupPlan) {
  const nearestSupport = asset.supports[0];
  const nearestResistance = asset.resistances[0];
  const risk = side === 'bullish' ? plan.entry - plan.stop : plan.stop - plan.entry;
  const reward = side === 'bullish' ? plan.target - plan.entry : plan.entry - plan.target;
  const rr = reward / Math.max(risk, 0.01);
  const location: LocationQuality = rr >= 1.8 ? 'clean' : rr >= 1.2 ? 'extended' : 'messy';

  if (side === 'bullish') {
    return {
      location,
      overview: `${asset.ticker} is acceptable only if the entry stays close to support instead of chasing into the first resistance zone. I want the long to start near $${plan.entry.toFixed(2)}, with invalidation close enough at $${plan.stop.toFixed(2)} and enough open air toward $${plan.target.toFixed(2)}.`,
      levelRead: `Nearest support is $${nearestSupport.toFixed(2)} and nearest resistance is $${nearestResistance.toFixed(2)}. A long is cleaner if price is holding above support without pressing directly into resistance.`,
      invalidationRead: `The stop is $${Math.abs(plan.entry - plan.stop).toFixed(2)} away from entry. That is close enough to define risk before the trade becomes emotional.`,
      roomRead: `The first target offers about ${rr.toFixed(2)}x reward-to-risk. I would not keep building if that drops below a clean two-sided payoff.`,
      volumeRead: 'The move needs expanding volume or a strong opening-range hold. Fading volume would tell me the entry is late.',
    };
  }

  return {
    location,
    overview: `${asset.ticker} is acceptable on the short side only if the entry is near failed support or rejected resistance, not after the move has already flushed. I want the short to start near $${plan.entry.toFixed(2)}, with invalidation tight at $${plan.stop.toFixed(2)} and room toward $${plan.target.toFixed(2)}.`,
    levelRead: `Nearest resistance is $${nearestResistance.toFixed(2)} and nearest support is $${nearestSupport.toFixed(2)}. A short is cleaner if price is rejecting resistance before it is already sitting on support.`,
    invalidationRead: `The stop is $${Math.abs(plan.stop - plan.entry).toFixed(2)} away from entry. If that distance widens, the short becomes a chase.`,
    roomRead: `The first target offers about ${rr.toFixed(2)}x reward-to-risk. The setup needs enough downside before the next support zone.`,
    volumeRead: 'The move needs expanding sell volume or a failed reclaim. If sellers are not pressing, the short is too messy.',
  };
}

function LevelsPanel({
  asset,
  side,
  plan,
  levels,
  metrics,
  detail,
  onDetail,
  onLevelsChange,
  onSave,
}: {
  asset: DemoAsset;
  side: Side;
  plan: SetupPlan;
  levels: Levels;
  metrics: { risk: number; reward: number; rr: number; valid: boolean };
  detail: Detail;
  onDetail: (detail: Detail) => void;
  onLevelsChange: (levels: Levels) => void;
  onSave: () => void;
}) {
  const warning =
    side === 'bullish'
      ? 'Stop needs to be below entry and target above entry for a long.'
      : 'Target needs to be below entry and stop above entry for a short.';

  return (
    <FocusCard label="Levels" title="Move the levels until the risk feels right.">
      <TypedBullets
        items={[
          'I suggested the first pass.',
          'You can change entry, take profit, and stop loss.',
          'This is the position-control layer: entry, invalidation, reward, then no trade if the math breaks.',
        ]}
      />
      <div className="grid gap-3">
        <LevelInput label="Entry" value={levels.entry} onChange={(entry) => onLevelsChange({ ...levels, entry })} />
        <LevelInput label="Take profit" value={levels.target} onChange={(target) => onLevelsChange({ ...levels, target })} />
        <LevelInput label="Stop loss" value={levels.stop} onChange={(stop) => onLevelsChange({ ...levels, stop })} />
      </div>
      <div className="mt-4 grid grid-cols-3 gap-px overflow-hidden rounded-2xl bg-white/10">
        <Metric label="Risk/share" value={`$${Math.max(metrics.risk, 0).toFixed(2)}`} />
        <Metric label="Reward/share" value={`$${Math.max(metrics.reward, 0).toFixed(2)}`} />
        <Metric label="R:R" value={metrics.valid ? `${metrics.rr.toFixed(2)}x` : 'Invalid'} />
      </div>
      {!metrics.valid && (
        <div className="mt-3 rounded-2xl border border-red/30 bg-red-bg px-3 py-2 text-[12.5px] font-medium text-red">
          {warning}
        </div>
      )}
      <DetailToggles detail={detail} onDetail={onDetail} enabled={['breaks']} />
      {detail === 'breaks' && <DetailPanel items={plan.invalidation} />}
      <PrimaryAction onClick={onSave} disabled={!metrics.valid}>
        Save demo setup
      </PrimaryAction>
      <div className="mt-3 text-center text-[11px] font-medium text-[#786f63]">
        Demo only. No order will be placed.
      </div>
      <span className="sr-only">{asset.ticker}</span>
    </FocusCard>
  );
}

function FinalPanel({
  asset,
  side,
  levels,
  metrics,
  plan,
  onRestart,
}: {
  asset: DemoAsset;
  side: Side | null;
  levels: Levels | null;
  metrics: { risk: number; reward: number; rr: number; valid: boolean } | null;
  plan: SetupPlan | null;
  onRestart: () => void;
}) {
  if (!side || !levels || !metrics || !plan) {
    return (
      <FocusCard label="Decision" title="No trade is a position.">
        <TypedBullets
          items={[
            'I would wait for a cleaner trigger instead of forcing this setup.',
            'The next good trade starts with a better level.',
          ]}
        />
        <PrimaryAction onClick={onRestart}>Start over</PrimaryAction>
      </FocusCard>
    );
  }

  const action = side === 'bullish' ? 'Long' : 'Short';
  return (
    <FocusCard label="Demo saved" title={`${action} ${asset.ticker}. Risk defined.`}>
      <TypedBullets
        items={[
          `Direction: ${action} ${asset.ticker}.`,
          `Entry: $${levels.entry.toFixed(2)}. Target: $${levels.target.toFixed(2)}. Stop: $${levels.stop.toFixed(2)}.`,
          `R:R: ${metrics.rr.toFixed(2)}x.`,
        ]}
      />
      <div className="mb-4 rounded-2xl border border-[#d6a84f]/30 bg-[#2a2112] px-3 py-2 text-[12.5px] font-medium text-[#f0c76b]">
        No order placed. This is a demo trade setup.
      </div>
      <div className="grid grid-cols-3 gap-px overflow-hidden rounded-2xl bg-white/10">
        <Metric label="Entry" value={`$${levels.entry.toFixed(2)}`} />
        <Metric label="TP" value={`$${levels.target.toFixed(2)}`} />
        <Metric label="SL" value={`$${levels.stop.toFixed(2)}`} />
      </div>
      <div className="mt-3 rounded-2xl border border-[#6b5cff]/30 bg-[#191733] px-3 py-2 text-[13px] font-semibold text-[#c9c3ff]">
        R:R {metrics.rr.toFixed(2)}x. The trade only works if the invalidation stays intact.
      </div>
      <div className="mt-4 grid gap-1.5">
        {plan.invalidation.map((item) => (
          <div key={item} className="rounded-xl bg-white/[0.04] px-3 py-2 text-[12.5px] leading-snug text-[#b9b1a4]">
            {item}
          </div>
        ))}
      </div>
      <PrimaryAction onClick={onRestart}>Build another setup</PrimaryAction>
    </FocusCard>
  );
}

function TopAgentDrawer({ run, onDone }: { run: ActiveRun; onDone: () => void }) {
  const codeLines = useMemo(() => buildCodeLines(run.lines, run.sources, run.conclusion), [run]);
  const [startedAt, setStartedAt] = useState(() => performance.now());
  const [elapsedMs, setElapsedMs] = useState(0);
  const [lineIndex, setLineIndex] = useState(0);
  const [charIndex, setCharIndex] = useState(0);
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    setStartedAt(performance.now());
    setElapsedMs(0);
    setLineIndex(0);
    setCharIndex(0);
    setFinished(false);
  }, [codeLines]);

  useEffect(() => {
    if (finished) return;
    const timer = window.setInterval(() => setElapsedMs(performance.now() - startedAt), 100);
    return () => window.clearInterval(timer);
  }, [finished, startedAt]);

  useEffect(() => {
    if (!run.durationMs || finished) return;
    const timer = window.setTimeout(() => {
      setFinished(true);
      onDone();
    }, run.durationMs);
    return () => window.clearTimeout(timer);
  }, [finished, onDone, run.durationMs]);

  useEffect(() => {
    if (finished) return;
    if (lineIndex < codeLines.length) {
      const current = codeLines[lineIndex] ?? '';
      if (charIndex < current.length) {
        const timer = window.setTimeout(() => setCharIndex((idx) => idx + 1), 22);
        return () => window.clearTimeout(timer);
      }
      const timer = window.setTimeout(() => {
        setLineIndex((idx) => idx + 1);
        setCharIndex(0);
      }, 220);
      return () => window.clearTimeout(timer);
    }
    const remainingMs = Math.max(0, (run.minDurationMs ?? 0) - (performance.now() - startedAt));
    const timer = window.setTimeout(() => {
      setFinished(true);
      onDone();
    }, Math.max(460, remainingMs));
    return () => window.clearTimeout(timer);
  }, [charIndex, codeLines, finished, lineIndex, onDone, run.durationMs, run.minDurationMs, startedAt]);

  const start = Math.max(0, lineIndex - 3);
  const visibleRows = Array.from({ length: 4 }, (_, row) => {
    const index = start + row;
    if (index > lineIndex || index >= codeLines.length) return { key: `blank-${row}`, text: '', active: false };
    const full = codeLines[index] ?? '';
    return {
      key: `${index}-${full}`,
      text: index === lineIndex ? full.slice(0, charIndex) : full,
      active: index === lineIndex,
    };
  });

  return (
    <div className="border-b border-white/10 bg-[#0b0a09]">
      <div className="mx-auto w-full max-w-[560px] px-4 pb-3 pt-2 sm:px-6">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="min-w-0 truncate text-[11px] font-medium text-[#8c8578]">{run.title}</div>
          <ThinkingTimer elapsedMs={elapsedMs} />
        </div>
        <div className="h-[106px] overflow-hidden border border-white/10 bg-[#11100f] px-3 py-2 font-mono text-[11px] leading-[22px] text-[#c8c6be] shadow-inner">
          {visibleRows.map((row) => (
            <div key={row.key} className="h-[22px] whitespace-pre overflow-hidden">
              {row.text}
              {row.active && <span className="ml-px inline-block h-3 w-px bg-[#c8c6be] align-middle animate-caret-blink" />}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function TickerSelectBar({
  selected,
  phase,
  briefComplete,
  tickerStage,
  onOpen,
  onChooseBias,
  locationReady,
}: {
  selected: DemoAsset | null;
  phase: Phase;
  briefComplete: boolean;
  tickerStage: 'brief' | 'exiting' | 'picker';
  onOpen: () => void;
  onChooseBias: (bias: Bias) => void;
  locationReady: boolean;
}) {
  const label = bottomBarLabel({ selected, phase, briefComplete, tickerStage, locationReady });

  if (selected && phase === 'technical' && locationReady) {
    return null;
  }

  return (
    <div
      className="fixed inset-x-0 z-50 bg-[#070706] px-4 pt-3 sm:px-6"
      style={{
        bottom: 'var(--keyboard-inset, 0px)',
        paddingBottom: 'max(16px, env(safe-area-inset-bottom, 0px))',
      }}
    >
      <div className="mx-auto max-w-[560px]">
        {selected && phase === 'direction' ? (
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => onChooseBias('bullish')}
              className="h-14 rounded-2xl bg-[#2fd17c] text-[14px] font-semibold text-[#04140b] shadow-lg shadow-[#2fd17c]/20 active:scale-[0.99]"
            >
              Bullish
            </button>
            <button
              type="button"
              onClick={() => onChooseBias('bearish')}
              className="h-14 rounded-2xl bg-[#ef4444] text-[14px] font-semibold text-[#210505] shadow-lg shadow-[#ef4444]/20 active:scale-[0.99]"
            >
              Bearish
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={onOpen}
            className="ticker-input-shell relative flex min-h-[68px] w-full items-center overflow-hidden rounded-[28px] bg-[#1b1917] px-5 text-left shadow-2xl shadow-black/40 outline-none active:scale-[0.99] focus:outline-none focus-visible:outline-none"
          >
            <span className="min-w-0 flex-1 truncate text-[18px] font-medium text-[#f7f1e6]">
              {label === 'ticker-prompt' ? <TickerPrompt /> : label === 'enter-ticker' ? <EnterTickerPrompt /> : label}
            </span>
          </button>
        )}
      </div>
    </div>
  );
}

function bottomBarLabel({
  selected,
  phase,
  briefComplete,
  tickerStage,
  locationReady,
}: {
  selected: DemoAsset | null;
  phase: Phase;
  briefComplete: boolean;
  tickerStage: 'brief' | 'exiting' | 'picker';
  locationReady: boolean;
}) {
  if (!selected && phase === 'brief' && briefComplete && tickerStage === 'brief') return 'ticker-prompt';
  if (!selected && phase === 'brief' && tickerStage === 'picker') return 'enter-ticker';
  if (!selected) return null;

  const labels: Partial<Record<Phase, string>> = {
    preflight: `Reading $${selected.ticker}`,
    catalyst: 'Review thesis',
    macro_work: 'Checking macro',
    macro: 'Check macro',
    direction: 'Choose direction',
    technical_work: 'Checking location',
    technical: locationReady ? 'Setup ready' : 'Analyzing location',
    setup_work: 'Building setup',
    levels: 'Adjust levels',
    final: 'Find another trade',
  };

  return labels[phase] ?? 'Find a trade';
}

function TickerLogo({ ticker, name, logoUrl }: { ticker: AssetKey; name: string; logoUrl: string | null }) {
  const [failed, setFailed] = useState(false);

  if (logoUrl && !failed) {
    return (
      <span className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full border border-[#34312e] bg-[#0d0c0b]">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={logoUrl} alt={`${name} logo`} className="h-6 w-6 object-contain" onError={() => setFailed(true)} />
      </span>
    );
  }

  return (
    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[#34312e] bg-[#0d0c0b] font-mono text-[12px] font-semibold text-[#f5f1e8]">
      {ticker.slice(0, 2)}
    </span>
  );
}

function FocusCard({ label, title, children }: {
  label: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="animate-slide-in px-1 py-2">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-[#4a96ff]">{label}</div>
      <h2 className="mb-4 text-2xl font-semibold leading-tight tracking-normal text-[#f7f1e6]">{title}</h2>
      {children}
    </section>
  );
}

function TickerPrompt() {
  const options = ['Or search $Micron', 'Or search $Gold', 'Or search $SPY'];
  const [optionIndex, setOptionIndex] = useState(0);
  const [charIndex, setCharIndex] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const current = options[optionIndex] ?? '';

  useEffect(() => {
    const atFullLine = charIndex === current.length;
    const atEmptyLine = charIndex === 0;
    const delay = atFullLine && !deleting ? 1400 : atEmptyLine && deleting ? 420 : deleting ? 62 : 95;

    const timer = window.setTimeout(() => {
      if (!deleting && charIndex < current.length) {
        setCharIndex((idx) => idx + 1);
        return;
      }

      if (!deleting && atFullLine) {
        setDeleting(true);
        return;
      }

      if (deleting && charIndex > 0) {
        setCharIndex((idx) => idx - 1);
        return;
      }

      setDeleting(false);
      setOptionIndex((idx) => (idx + 1) % options.length);
    }, delay);

    return () => window.clearTimeout(timer);
  }, [charIndex, current.length, deleting, optionIndex, options.length]);

  return (
    <span className="text-[#f7f1e6]">
      {current.slice(0, charIndex)}
      <span className="ml-px inline-block h-[0.9em] w-px bg-current align-middle animate-caret-blink" />
    </span>
  );
}

function EnterTickerPrompt() {
  return (
    <span className="text-[#f7f1e6]">
      Enter Ticker
      <span className="ml-1 inline-block h-[0.9em] w-px bg-current align-middle animate-caret-blink" />
    </span>
  );
}

function TerminalStrip({ label }: { label: string }) {
  return (
    <div className="flex h-8 items-center gap-2 border-b border-white/10 bg-[#0b0a09] px-4 pr-16 font-mono text-[11px] text-[#b9b1a4]">
      <span className="live-dot shrink-0" />
      <span className="truncate">agent status: {label}</span>
    </div>
  );
}

function TypedBullets({ items }: { items: string[] }) {
  const seed = items.join('\n');
  const [itemIndex, setItemIndex] = useState(0);
  const [charIndex, setCharIndex] = useState(0);

  useEffect(() => {
    setItemIndex(0);
    setCharIndex(0);
  }, [seed]);

  useEffect(() => {
    if (itemIndex >= items.length) return;
    const current = items[itemIndex] ?? '';
    if (charIndex < current.length) {
      const timer = window.setTimeout(() => setCharIndex((idx) => idx + 1), 24);
      return () => window.clearTimeout(timer);
    }
    const timer = window.setTimeout(() => {
      setItemIndex((idx) => idx + 1);
      setCharIndex(0);
    }, 260);
    return () => window.clearTimeout(timer);
  }, [charIndex, itemIndex, items]);

  const visibleItems = items.slice(0, Math.min(items.length, itemIndex + 1));

  return (
    <ul className="mb-4 grid gap-2">
      {visibleItems.map((item, index) => {
        const visible = index < itemIndex ? item : index === itemIndex ? item.slice(0, charIndex) : '';
        return (
          <li
            key={item}
            className="flex min-h-[34px] gap-2.5 rounded-2xl border border-white/[0.06] bg-white/[0.04] px-3 py-2 text-[13px] leading-snug text-[#c9c0b2]"
          >
            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-[#8f84ff]" />
            <span className="flex-1">
              {visible}
              {index === itemIndex && charIndex < item.length && (
                <span className="ml-px inline-block h-3 w-px bg-[#c8c6be] align-middle animate-caret-blink" />
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function TypedBriefBullets({ items, onDone }: { items: string[]; onDone?: () => void }) {
  const seed = items.join('\n');
  const [itemIndex, setItemIndex] = useState(0);
  const [charIndex, setCharIndex] = useState(0);
  const notifiedRef = useRef(false);

  useEffect(() => {
    setItemIndex(0);
    setCharIndex(0);
    notifiedRef.current = false;
  }, [seed]);

  useEffect(() => {
    if (itemIndex >= items.length) return;
    const current = items[itemIndex] ?? '';
    if (charIndex < current.length) {
      const timer = window.setTimeout(() => setCharIndex((idx) => idx + 1), 24);
      return () => window.clearTimeout(timer);
    }
    const timer = window.setTimeout(() => {
      setItemIndex((idx) => idx + 1);
      setCharIndex(0);
    }, 260);
    return () => window.clearTimeout(timer);
  }, [charIndex, itemIndex, items]);

  useEffect(() => {
    if (itemIndex < items.length || notifiedRef.current) return;
    notifiedRef.current = true;
    onDone?.();
  }, [itemIndex, items.length, onDone]);

  const visibleItems = items.slice(0, Math.min(items.length, itemIndex + 1));

  return (
    <ul className="mt-5 grid gap-3">
      {visibleItems.map((item, index) => {
        const visible = index < itemIndex ? item : index === itemIndex ? item.slice(0, charIndex) : '';
        return (
          <li
            key={item}
            className="flex min-h-[42px] gap-3 text-[14px] leading-relaxed text-[#c9c0b2]"
          >
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#4a96ff]" />
            <span className="flex-1">
              {visible}
              {index === itemIndex && charIndex < item.length && (
                <span className="ml-px inline-block h-3 w-px bg-[#c8c6be] align-middle animate-caret-blink" />
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function TypedText({
  text,
  speedMs = 24,
  startDelayMs = 0,
  onDone,
}: {
  text: string;
  speedMs?: number;
  startDelayMs?: number;
  onDone?: () => void;
}) {
  const [charIndex, setCharIndex] = useState(0);
  const notifiedRef = useRef(false);

  useEffect(() => {
    setCharIndex(0);
    notifiedRef.current = false;
  }, [text]);

  useEffect(() => {
    if (charIndex >= text.length) return;
    const delay = charIndex === 0 ? startDelayMs : speedMs;
    const timer = window.setTimeout(() => setCharIndex((idx) => idx + 1), delay);
    return () => window.clearTimeout(timer);
  }, [charIndex, speedMs, startDelayMs, text]);

  useEffect(() => {
    if (charIndex < text.length || notifiedRef.current) return;
    notifiedRef.current = true;
    onDone?.();
  }, [charIndex, onDone, text.length]);

  return (
    <>
      {text.slice(0, charIndex)}
      {charIndex < text.length && (
        <span className="ml-px inline-block h-[0.8em] w-px bg-current align-middle animate-caret-blink" />
      )}
    </>
  );
}

function TypedHeadline({
  prefix,
  highlight,
  speedMs,
  startDelayMs = 0,
  onDone,
}: {
  prefix: string;
  highlight: string;
  speedMs: number;
  startDelayMs?: number;
  onDone?: () => void;
}) {
  const text = `${prefix}${highlight}`;
  const [charIndex, setCharIndex] = useState(0);
  const notifiedRef = useRef(false);

  useEffect(() => {
    setCharIndex(0);
    notifiedRef.current = false;
  }, [text]);

  useEffect(() => {
    if (charIndex >= text.length) return;
    const delay = charIndex === 0 ? startDelayMs : speedMs;
    const timer = window.setTimeout(() => setCharIndex((idx) => idx + 1), delay);
    return () => window.clearTimeout(timer);
  }, [charIndex, speedMs, startDelayMs, text]);

  useEffect(() => {
    if (charIndex < text.length || notifiedRef.current) return;
    notifiedRef.current = true;
    onDone?.();
  }, [charIndex, onDone, text.length]);

  const visiblePrefix = text.slice(0, Math.min(charIndex, prefix.length));
  const visibleHighlight = charIndex > prefix.length ? text.slice(prefix.length, charIndex) : '';

  return (
    <>
      {visiblePrefix}
      <span className="text-[#1f7a4d]">{visibleHighlight}</span>
      {charIndex < text.length && (
        <span className="ml-px inline-block h-[0.8em] w-px bg-current align-middle animate-caret-blink" />
      )}
    </>
  );
}

function MacroFactorTile({ factor, compact = false }: { factor: LiveMacroFactor; compact?: boolean }) {
  const toneClass = factor.tone === 'green' ? 'text-[#29b987]' : factor.tone === 'blue' ? 'text-[#91c7ff]' : 'text-[#d6c8bc]';
  const flashClass =
    factor.flash === 'up'
      ? 'bg-[#123326] text-[#67d6a3]'
      : factor.flash === 'down'
        ? 'bg-[#351818] text-[#ff8d8d]'
        : toneClass;

  if (compact) {
    return (
      <div className="flex h-8 shrink-0 items-center gap-2 border border-white/[0.08] bg-[#11100f] px-3 font-mono text-[11px]">
        <span className="font-semibold text-[#8c8578]">{factor.label}</span>
        <span className={`rounded px-1 py-0.5 font-semibold transition-colors duration-200 ${flashClass}`}>
          {formatMacroValue(factor.value, factor.decimals)}
        </span>
        <span className="text-[#8c8578]">{factor.change}</span>
      </div>
    );
  }

  return (
    <div className="h-[142px] w-[176px] shrink-0 overflow-hidden rounded-[14px] border border-white/[0.08] bg-[#1b1b1b] p-3">
      <div className="text-[12px] font-semibold uppercase tracking-normal text-[#9a9da0]">{factor.label}</div>
      <div className={`mt-4 inline-block rounded-md px-1.5 py-0.5 text-[28px] font-semibold leading-none transition-colors duration-200 ${flashClass}`}>
        {formatMacroValue(factor.value, factor.decimals)}
      </div>
      <div className="mt-3 text-[13px] font-medium text-[#a9aeb3]">{factor.change}</div>
      <Sparkline points={factor.points} tone={factor.tone} />
    </div>
  );
}

function Sparkline({ points, tone }: { points: number[]; tone: MacroFactor['tone'] }) {
  const stroke = tone === 'blue' ? '#91c7ff' : tone === 'green' ? '#29b987' : '#d6c8bc';
  const width = 106;
  const height = 38;
  const pad = 3;
  const xPad = 2;
  const max = Math.max(...points);
  const min = Math.min(...points);
  const range = Math.max(1, max - min);
  const d = points
    .map((point, index) => {
      const x = xPad + (index / (points.length - 1)) * (width - xPad * 2);
      const y = pad + (1 - (point - min) / range) * (height - pad * 2);
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg className="mt-2 h-[38px] w-full overflow-visible" viewBox={`0 0 ${width} ${height}`} aria-hidden="true" preserveAspectRatio="none">
      <path d={d} fill="none" stroke={stroke} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PrimaryAction({
  children,
  onClick,
  disabled = false,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="mt-5 h-12 w-full rounded-2xl bg-[#f5f1e8] px-4 text-sm font-semibold text-[#11100f] shadow-lg shadow-black/30 active:scale-[0.98] disabled:opacity-40"
    >
      {children}
    </button>
  );
}

function SecondaryAction({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mt-3 w-full rounded-2xl px-4 py-2.5 text-sm font-semibold text-[#8c8578] active:scale-[0.98]"
    >
      {children}
    </button>
  );
}

function DetailToggles({
  detail,
  onDetail,
  enabled,
}: {
  detail: Detail;
  onDetail: (detail: Detail) => void;
  enabled: Array<Exclude<Detail, null>>;
}) {
  const labels: Record<Exclude<Detail, null>, string> = {
    why: 'Why?',
    sources: 'Sources',
    breaks: 'What breaks this?',
  };
  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {enabled.map((item) => (
        <button
          key={item}
          type="button"
          onClick={() => onDetail(detail === item ? null : item)}
          className={`rounded-full border px-3 py-1.5 text-[12px] font-semibold ${
            detail === item
              ? 'border-[#8f84ff] bg-[#191733] text-[#c9c3ff]'
              : 'border-white/10 bg-white/[0.04] text-[#8c8578]'
          }`}
        >
          {labels[item]}
        </button>
      ))}
    </div>
  );
}

function DetailPanel({ items }: { items: string[] }) {
  return (
    <div className="mt-3 grid gap-1.5 rounded-2xl border border-white/10 bg-[#0d0c0b] p-2">
      {items.map((item) => (
        <div key={item} className="rounded-xl bg-white/[0.04] px-3 py-2 text-[12.5px] leading-snug text-[#b9b1a4]">
          {item}
        </div>
      ))}
    </div>
  );
}

function ChoiceButton({
  title,
  body,
  active,
  recommended,
  onClick,
}: {
  title: string;
  body: string;
  active: boolean;
  recommended: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-2xl border p-3 text-left active:scale-[0.99] ${
        active
          ? 'border-[#8f84ff] bg-[#191733]'
          : recommended
            ? 'border-[#8f84ff]/40 bg-white/[0.06]'
            : 'border-white/10 bg-white/[0.04]'
      }`}
    >
      <span className="flex items-center gap-2">
        <span className="text-sm font-semibold text-[#f5f1e8]">{title}</span>
        {recommended && (
          <span className="rounded-full bg-[#6b5cff] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-white">
            My lean
          </span>
        )}
      </span>
      <span className="mt-1 block text-[12.5px] leading-snug text-[#a9a194]">{body}</span>
    </button>
  );
}

function LevelGroup({ label, values, tone }: { label: string; values: number[]; tone: 'green' | 'red' }) {
  const toneClass = tone === 'green' ? 'text-[#67d6a3] bg-[#10281e]' : 'text-[#ff8d8d] bg-[#2b1414]';
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.04] px-3 py-2.5">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#8c8578]">{label}</div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {values.map((level) => (
          <span key={level} className={`rounded-full px-2 py-1 text-[11px] font-semibold ${toneClass}`}>
            ${level.toFixed(2)}
          </span>
        ))}
      </div>
    </div>
  );
}

function LevelInput({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="grid grid-cols-[1fr_132px] items-center gap-3">
      <span className="text-sm font-semibold text-[#c9c0b2]">{label}</span>
      <input
        type="number"
        inputMode="decimal"
        step="0.1"
        value={Number.isFinite(value) ? value : 0}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-11 rounded-xl border border-white/10 bg-[#0d0c0b] px-3 text-right text-sm font-semibold text-[#f5f1e8] outline-none focus:border-[#8f84ff]"
      />
    </label>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[#0d0c0b] px-3 py-2.5 text-center">
      <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[#8c8578]">{label}</div>
      <div className="mt-0.5 text-sm font-semibold text-[#f5f1e8]">{value}</div>
    </div>
  );
}

function ThinkingTimer({ elapsedMs }: { elapsedMs: number }) {
  const totalSeconds = elapsedMs / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = (totalSeconds % 60).toFixed(1).padStart(4, '0');
  return (
    <div className="shrink-0 rounded-md bg-[#0b0808] px-2.5 py-1 font-mono text-[12px] text-[#c8c6be]">
      . {minutes}m, {seconds}s
    </div>
  );
}

function buildCodeLines(lines: string[], sources: string[], conclusion: string) {
  const sourceList = sources.map((source) => `'${source.toLowerCase().replace(/'/g, '')}'`).join(', ');
  return [
    'import numpy as np',
    'import pandas as pd',
    'from desk.risk import reward_to_risk',
    '',
    `sources = [${sourceList}]`,
    'book = MarketNotebook(sources=sources)',
    ...lines.map((line) => (line.includes('=') ? line : `book.${line}`)),
    '',
    'levels = book.key_levels()',
    'bias = book.directional_bias()',
    'invalid = book.invalidation()',
    'setup = reward_to_risk(levels, invalid)',
    '',
    `summary = '${conclusion.replace(/'/g, '')}'`,
    'return TradeRead(bias, levels, setup, summary)',
  ];
}

function biasLabel(bias: Side) {
  return bias === 'bullish' ? 'Bullish' : 'Bearish';
}

function titleCase(value: string) {
  return value
    .split(' ')
    .map((word) => word.slice(0, 1).toUpperCase() + word.slice(1))
    .join(' ');
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function formatMacroValue(value: number, decimals: number) {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function phaseAfter(target: Phase, current: Phase) {
  const order: Phase[] = [
    'empty',
    'brief_work',
    'brief',
    'preflight',
    'catalyst',
    'macro_work',
    'macro',
    'direction',
    'technical_work',
    'technical',
    'setup_work',
    'levels',
    'final',
  ];
  return order.indexOf(current) > order.indexOf(target);
}

function statusForPhase(phase: Phase) {
  const labels: Record<Phase, string> = {
    empty: 'agent is live',
    brief_work: 'building daily brief',
    brief: 'daily brief rendered',
    preflight: 'scanning market',
    catalyst: 'catalyst rendered',
    macro_work: 'reading macro',
    macro: 'macro rendered',
    direction: 'building directional bias',
    technical_work: 'checking trade location',
    technical: 'trade location ready',
    setup_work: 'building levels',
    levels: 'levels rendered',
    final: 'setup complete',
  };
  return labels[phase];
}
