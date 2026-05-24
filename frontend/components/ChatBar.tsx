'use client';

import { useEffect, useRef, useState } from 'react';

const PLACEHOLDER_PROMPTS = [
  "Brief me on this morning",
  "Why did NVDA move 4% today?",
  "Are there red flags in AAPL's 10-K?",
  "Steelman the bear case on TSLA",
  "Compare AMD and NVDA on valuation",
  "Audit my portfolio for concentration",
  "How much should I size NVDA?",
  "What should I watch in NVDA's print?",
  "Add the 50 and 200-day SMA",
];

type Props = {
  onSubmit: (text: string) => void;
  onMicTap?: () => void;
  disabled?: boolean;
};

export function ChatBar({ onSubmit, onMicTap, disabled }: Props) {
  const [value, setValue] = useState('');
  const [phIndex, setPhIndex] = useState(0);
  const [phText, setPhText] = useState('');
  const [phPhase, setPhPhase] = useState<'typing' | 'pausing' | 'erasing'>('typing');
  const inputRef = useRef<HTMLInputElement>(null);

  // Animated placeholder loop
  useEffect(() => {
    const target = PLACEHOLDER_PROMPTS[phIndex];
    let timer: ReturnType<typeof setTimeout>;
    if (phPhase === 'typing') {
      if (phText.length < target.length) {
        timer = setTimeout(() => setPhText(target.slice(0, phText.length + 1)), 40 + Math.random() * 30);
      } else {
        timer = setTimeout(() => setPhPhase('pausing'), 1800);
      }
    } else if (phPhase === 'pausing') {
      timer = setTimeout(() => setPhPhase('erasing'), 150);
    } else {
      if (phText.length > 0) {
        timer = setTimeout(() => setPhText(phText.slice(0, -1)), 18);
      } else {
        setPhIndex((i) => (i + 1) % PLACEHOLDER_PROMPTS.length);
        setPhPhase('typing');
      }
    }
    return () => clearTimeout(timer);
  }, [phText, phPhase, phIndex]);

  const submit = () => {
    const t = value.trim();
    if (!t || disabled) return;
    setValue('');
    onSubmit(t);
  };

  const showPlaceholder = value.length === 0;

  return (
    <div className="absolute inset-x-0 bottom-0 z-50 px-4 pb-6 pt-3 bg-gradient-to-t from-bg via-bg/95 to-transparent">
      <div className="flex items-center gap-2.5">
        <div className="relative flex-1">
          <div className="absolute left-4 top-1/2 -translate-y-1/2 pointer-events-none">
            <span className="pulse-dot block" />
          </div>
          {/* Animated placeholder when input is empty */}
          {showPlaceholder && (
            <div className="absolute left-9 top-1/2 -translate-y-1/2 pointer-events-none text-text-3 text-sm">
              {phText}
              <span className="inline-block w-px h-3 bg-text-3 ml-px align-middle animate-caret-blink" />
            </div>
          )}
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            disabled={disabled}
            className="w-full h-12 bg-surface border border-border rounded-3xl pl-9 pr-4 text-sm text-text outline-none focus:border-accent transition-colors"
          />
        </div>
        <button
          type="button"
          onClick={onMicTap}
          aria-label="Voice input"
          className="w-12 h-12 rounded-full bg-accent text-white flex items-center justify-center shadow-lg shadow-accent/30 active:scale-95 transition-transform"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
            <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
            <line x1="12" y1="18" x2="12" y2="22" />
            <line x1="8" y1="22" x2="16" y2="22" />
          </svg>
        </button>
      </div>
    </div>
  );
}
