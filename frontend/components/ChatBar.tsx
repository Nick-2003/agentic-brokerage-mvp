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
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the textarea with its content, capped at ~5 lines then scroll.
  // Reset-then-measure so it also SHRINKS when text is deleted (without the
  // `height='auto'` reset, scrollHeight only ever grows).
  const MAX_TEXTAREA_PX = 120; // ~5 lines at text-sm / leading-snug
  const autoGrow = (el: HTMLTextAreaElement) => {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_PX)}px`;
    el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_PX ? 'auto' : 'hidden';
  };

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
    // setValue('') does NOT fire onChange, so reset the grown height manually,
    // else the box stays tall after sending.
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
      inputRef.current.style.overflowY = 'hidden';
    }
    onSubmit(t);
  };

  const showPlaceholder = value.length === 0;

  return (
    <div className="absolute inset-x-0 bottom-0 z-50 px-4 pb-6 pt-3 bg-gradient-to-t from-bg via-bg/95 to-transparent">
      <div className="flex items-center gap-2.5">
        <div className="relative flex-1">
          {/* Pinned to the first line (not vertically centered) so it stays put
              when the textarea grows. */}
          <div className="absolute left-4 top-[18px] pointer-events-none">
            <span className="pulse-dot block" />
          </div>
          {/* Animated placeholder when input is empty (only ever shown at 1 line). */}
          {showPlaceholder && (
            <div className="absolute left-9 top-[14px] pointer-events-none text-text-3 text-sm">
              {phText}
              <span className="inline-block w-px h-3 bg-text-3 ml-px align-middle animate-caret-blink" />
            </div>
          )}
          <textarea
            ref={inputRef}
            rows={1}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              autoGrow(e.target);
            }}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter inserts a newline. preventDefault stops
              // the stray '\n' before we submit. Skip while an IME is composing.
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
            disabled={disabled}
            className="block w-full bg-surface border border-border rounded-2xl pl-9 pr-4 py-3 text-sm leading-snug text-text outline-none focus:border-accent transition-colors resize-none"
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
