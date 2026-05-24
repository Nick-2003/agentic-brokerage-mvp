'use client';

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

export function WidgetRenderer({ widget, onTrackerTap, onOrderConfirm, onOrderEdit }: Props) {
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
