'use client';

import type { ChatAttachment } from '@/lib/sse';
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

// 059 — vision input constraints, mirrored on the backend (main.py) so the user
// gets a friendly reject BEFORE upload. Images are downscaled to ~1568px (the
// Anthropic vision sweet spot) and re-encoded to JPEG, keeping the JSON body and
// per-image token cost small and predictable.
const MAX_ATTACHMENTS = 4;
const MAX_EDGE_PX = 1568;
const JPEG_QUALITY = 0.85;
const MAX_B64_CHARS = 7_000_000; // ~5 MB binary; matches backend per-item cap

// One pending upload held in the bar before send. `data` is raw base64 (no
// `data:` prefix — that's what the API wants); `previewUrl` is the full data URL
// for the thumbnail.
type PendingAttachment = {
  id: string;
  media_type: string;
  data: string;
  name?: string;
  previewUrl: string;
};

// Load → downscale (longest edge ≤ MAX_EDGE_PX) → JPEG data URL. Returns null if
// the file can't be decoded as an image.
function downscaleToJpeg(file: File): Promise<{ dataUrl: string } | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const { width, height } = img;
      const scale = Math.min(1, MAX_EDGE_PX / Math.max(width, height));
      const w = Math.max(1, Math.round(width * scale));
      const h = Math.max(1, Math.round(height * scale));
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        URL.revokeObjectURL(url);
        resolve(null);
        return;
      }
      ctx.drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url);
      resolve({ dataUrl: canvas.toDataURL('image/jpeg', JPEG_QUALITY) });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(null);
    };
    img.src = url;
  });
}

type Props = {
  onSubmit: (text: string, attachments: ChatAttachment[]) => void;
  onMicTap?: () => void;
  disabled?: boolean;
};

export function ChatBar({ onSubmit, onMicTap, disabled }: Props) {
  const [value, setValue] = useState('');
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [phIndex, setPhIndex] = useState(0);
  const [phText, setPhText] = useState('');
  const [phPhase, setPhPhase] = useState<'typing' | 'pausing' | 'erasing'>('typing');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Auto-grow the textarea with its content, capped at ~5 lines then scroll.
  // Reset-then-measure so it also SHRINKS when text is deleted (without the
  // `height='auto'` reset, scrollHeight only ever grows).
  const MAX_TEXTAREA_PX = 120; // ~5 lines at text-sm / leading-snug
  const autoGrow = (el: HTMLTextAreaElement) => {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_PX)}px`;
    el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_PX ? 'auto' : 'hidden';
  };

  // Animated placeholder loop (paused once the user has typed or attached).
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

  // Turn a list of picked/pasted files into pending attachments (downscaled),
  // honoring the count/size caps with a friendly note when something is dropped.
  const addFiles = async (files: File[]) => {
    const images = files.filter((f) => f.type.startsWith('image/'));
    if (images.length === 0) {
      if (files.length > 0) setNote('Only image files are supported.');
      return;
    }
    setNote(null);
    for (const file of images) {
      if (attachments.length >= MAX_ATTACHMENTS) {
        setNote(`You can attach up to ${MAX_ATTACHMENTS} images.`);
        break;
      }
      const res = await downscaleToJpeg(file);
      if (!res) {
        setNote(`Couldn't read "${file.name}".`);
        continue;
      }
      const data = res.dataUrl.split(',')[1] ?? '';
      if (data.length > MAX_B64_CHARS) {
        setNote(`"${file.name}" is too large even after resizing.`);
        continue;
      }
      setAttachments((prev) =>
        prev.length >= MAX_ATTACHMENTS
          ? prev
          : [
              ...prev,
              {
                id: crypto.randomUUID(),
                media_type: 'image/jpeg',
                data,
                name: file.name,
                previewUrl: res.dataUrl,
              },
            ],
      );
    }
  };

  const removeAttachment = (id: string) =>
    setAttachments((prev) => prev.filter((a) => a.id !== id));

  const submit = () => {
    const t = value.trim();
    if (disabled) return;
    if (!t && attachments.length === 0) return; // nothing to send
    const atts: ChatAttachment[] = attachments.map((a) => ({
      kind: 'image',
      media_type: a.media_type,
      data: a.data,
      name: a.name,
    }));
    setValue('');
    setAttachments([]);
    setNote(null);
    // setValue('') does NOT fire onChange, so reset the grown height manually,
    // else the box stays tall after sending.
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
      inputRef.current.style.overflowY = 'hidden';
    }
    onSubmit(t, atts);
  };

  // Show the animated placeholder only when there's no text AND no attachment.
  const showPlaceholder = value.length === 0 && attachments.length === 0;

  return (
    <div className="absolute inset-x-0 bottom-0 z-50 px-4 pb-6 pt-3 bg-bg">
      {/* Attachment thumbnails — shown above the bar so the user SEES what's
          being sent with the message (059). */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-2">
          {attachments.map((a) => (
            <div key={a.id} className="relative">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={a.previewUrl}
                alt={a.name || 'attachment'}
                className="w-14 h-14 object-cover rounded-lg border border-border"
              />
              <button
                type="button"
                onClick={() => removeAttachment(a.id)}
                aria-label="Remove attachment"
                className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-text text-white text-[11px] leading-none flex items-center justify-center shadow"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
      {note && <div className="mb-2 text-[12px] text-red px-1">{note}</div>}

      <div className="flex items-center gap-2.5">
        {/* Attach button + hidden file input (059). */}
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(e) => {
            void addFiles(Array.from(e.target.files ?? []));
            e.target.value = ''; // allow re-picking the same file
          }}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={disabled}
          aria-label="Attach image"
          className="w-10 h-10 shrink-0 rounded-full bg-surface border border-border text-text-2 flex items-center justify-center active:scale-95 transition-transform disabled:opacity-50"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
          </svg>
        </button>

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
            onPaste={(e) => {
              // 059 — paste an image straight from the clipboard.
              const files = Array.from(e.clipboardData?.items ?? [])
                .filter((it) => it.kind === 'file' && it.type.startsWith('image/'))
                .map((it) => it.getAsFile())
                .filter((f): f is File => !!f);
              if (files.length > 0) {
                e.preventDefault();
                void addFiles(files);
              }
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
