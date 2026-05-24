'use client';

export type Thought = {
  text: string;
  state: 'active' | 'done';
};

type Props = {
  thoughts: Thought[];
  done?: boolean;
  elapsedMs?: number;
};

export function ThinkingCard({ thoughts, done, elapsedMs }: Props) {
  return (
    <div className="bg-surface border border-border rounded-2xl p-4 mt-3 animate-slide-in">
      <div className="flex items-center gap-2 mb-3">
        {done ? (
          <div className="w-3 h-3 rounded-full border border-green-DEFAULT flex items-center justify-center">
            <span className="text-green-DEFAULT text-[10px] font-bold leading-none">✓</span>
          </div>
        ) : (
          <div className="w-3 h-3 rounded-full border border-accent border-t-transparent animate-spin-slow" />
        )}
        <span className={`text-[10px] font-semibold uppercase tracking-[0.14em] ${done ? 'text-green-DEFAULT' : 'text-accent'}`}>
          {done ? `Done · ${Math.round((elapsedMs ?? 0) / 1000)}s` : 'Thinking'}
        </span>
      </div>
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
                <span className="text-green-DEFAULT font-bold">✓</span>
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
