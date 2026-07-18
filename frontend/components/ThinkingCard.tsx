'use client';

export type Thought = {
  text: string;
  state: 'active' | 'done';
};

type Props = {
  thoughts: Thought[];
  done?: boolean;
  elapsedMs?: number;
  // 069 — which model answered this turn.
  provider?: { label: string; fallback: boolean; reason: string | null };
};

// 069 — map the failover reason code to a short human phrase for the notice.
function reasonPhrase(reason: string | null): string {
  if (reason === 'anthropic_billing') return 'usage limit reached';
  if (reason === 'anthropic_rate_limit') return 'rate limited';
  if (reason === 'anthropic_overloaded') return 'overloaded';
  return 'unavailable';
}

export function ThinkingCard({ thoughts, done, elapsedMs, provider }: Props) {
  return (
    <div className="bg-surface border border-border rounded-2xl p-4 mt-3 animate-slide-in">
      <div className="flex items-center gap-2 mb-3">
        {done ? (
          <div className="w-3 h-3 rounded-full border border-green flex items-center justify-center">
            <span className="text-green text-[10px] font-bold leading-none">✓</span>
          </div>
        ) : (
          <div className="w-3 h-3 rounded-full border border-accent border-t-transparent animate-spin-slow" />
        )}
        <span className={`text-[10px] font-semibold uppercase tracking-[0.14em] ${done ? 'text-green' : 'text-accent'}`}>
          {done ? `Done · ${Math.round((elapsedMs ?? 0) / 1000)}s` : 'Thinking'}
        </span>
        {/* 069 — model chip: amber when the answer came from the fallback rail. */}
        {provider && (
          <span
            className={`ml-auto text-[10px] font-semibold flex items-center gap-1 ${
              provider.fallback ? 'text-amber' : 'text-text-3'
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${provider.fallback ? 'bg-amber' : 'bg-text-3'}`} />
            {provider.label}
            {provider.fallback && ' · fallback'}
          </span>
        )}
      </div>
      {/* 069 — honest one-line notice when the fallback answered. */}
      {provider?.fallback && (
        <div className="mb-3 text-[11.5px] text-text-2 leading-snug">
          Claude was {reasonPhrase(provider.reason)} — this answer came from{' '}
          <span className="font-medium">{provider.label}</span>.
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        {thoughts.map((t, i) => (
          <div
            key={i}
            className={`flex gap-2.5 items-start text-[13px] leading-tight ${
              t.state === 'done' ? 'text-text-3' : 'text-text'
            }`}
          >
            <span className="w-3.5 flex justify-center text-[11px] leading-snug">
              {t.state === 'done' ? (
                <span className="text-green font-bold">✓</span>
              ) : (
                <span className="text-accent font-semibold">→</span>
              )}
            </span>
            <span className="flex-1">{t.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
