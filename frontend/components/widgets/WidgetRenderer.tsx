'use client';

import { Component, type ReactNode } from 'react';
import type { Widget } from '@/lib/widgets';
import { LiveTrade } from './LiveTrade';
import { MorningBrief } from './MorningBrief';
import { OrderTicket } from './OrderTicket';
import { PortfolioRisk } from './PortfolioRisk';
import { ResearchCard } from './ResearchCard';
import { TAChart } from './TAChart';
import { Thesis } from './Thesis';
import { Tracker } from './Tracker';

type Props = {
  widget: Widget;
  onTrackerTap?: () => void;
  onOrderConfirm?: () => void;
  onOrderEdit?: () => void;
};

// 055 — contain a single widget's render error so a malformed card (e.g. a null
// numeric field hitting `.toFixed`/`.toLocaleString`) shows a small fallback
// instead of crashing the whole page/turn (which previously white-screened the app).
class WidgetErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    console.error('widget render failed:', error);
  }
  render() {
    if (this.state.failed) {
      return (
        <div className="bg-surface border border-border rounded-2xl p-4 text-[12.5px] text-text-3">
          This card couldn’t be displayed.
        </div>
      );
    }
    return this.props.children;
  }
}

export function WidgetRenderer(props: Props) {
  return (
    <WidgetErrorBoundary>
      <WidgetInner {...props} />
    </WidgetErrorBoundary>
  );
}

function WidgetInner({ widget, onTrackerTap, onOrderConfirm, onOrderEdit }: Props) {
  switch (widget.type) {
    case 'morning_brief':
      return <MorningBrief data={widget.data} sources={widget.sources} />;
    case 'research_card':
      return <ResearchCard data={widget.data} sources={widget.sources} />;
    case 'ta_chart':
      return <TAChart data={widget.data} sources={widget.sources} />;
    case 'order_ticket':
      return (
        <OrderTicket
          data={widget.data}
          sources={widget.sources}
          onConfirm={onOrderConfirm}
          onEdit={onOrderEdit}
        />
      );
    case 'live_trade':
      return <LiveTrade data={widget.data} sources={widget.sources} />;
    case 'thesis':
      return <Thesis data={widget.data} sources={widget.sources} />;
    case 'tracker':
      return <Tracker data={widget.data} sources={widget.sources} onTap={onTrackerTap} />;
    case 'portfolio_risk':
      return <PortfolioRisk data={widget.data} sources={widget.sources} />;
    default:
      return null;
  }
}
