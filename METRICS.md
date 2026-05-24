# METRICS.md — measurement framework

**Lock these benchmarks in BEFORE launch.** Per the MVP guide: founders who define metrics after launch end up choosing the ones that flatter their early traction. We don't.

## What we're measuring (and why)

We're optimising for *evidence of product-market fit*, not feature completion. PMF lives in three signals:

| Signal | What it tells us |
|---|---|
| **Retention** | Are users coming back without prompting? |
| **Activation** | Do users get to a "magic moment" in their first session? |
| **Referral** | Do users tell others about it without being asked? |

The Sean Ellis test (40%+ "very disappointed") is the gold standard once we have ≥30 actively engaged users.

## Primary metrics (the dashboard)

### Retention

| Cohort | Target | Stretch |
|---|---|---|
| **D1 retention** | ≥50% | ≥65% |
| **D7 retention** | ≥30% | ≥45% |
| **D30 retention** | ≥20% | ≥35% (Sean Ellis zone) |

Defined as: a user who created an account on day N and returned (any prompt submitted) on day N+K.

### Activation

**Activation event:** first prompt submitted → at least one widget pinned to home within the same session.

| Metric | Target |
|---|---|
| Activation rate (within first session) | ≥80% |
| Median time from signup to activation | <10 min |

If a user signs up and never pins anything, they're not activated. That's our cleanest proxy for "they got value."

### Engagement depth

| Metric | Target |
|---|---|
| Prompts per session by D7 | ≥5 |
| Sessions per active user per week | ≥3 by D7 |
| Median widget pins per user | ≥4 by D14 |

If users come back but each session is one prompt and they leave, that's curiosity not utility.

### Trade-flow validation

Specific to this product — does the agent actually drive trade intent?

| Metric | Target |
|---|---|
| % of active users who place ≥1 paper trade | ≥60% by D14 |
| Trades placed per active user per week | ≥1.5 by D7 |

Paper trades only — but the *intent* signal is what matters. If nobody ever wants to actually trade off the agent's recommendations, the product is a research tool, not a brokerage.

## Sean Ellis test (PMF survey)

Sent to all active users at D14 and again at D30:

> *"How would you feel if you could no longer use this product?"*
> - Very disappointed
> - Somewhat disappointed
> - Not disappointed
> - I no longer use it

**PMF target:** ≥40% answer "very disappointed."

Below 40%: we don't have PMF yet — iterate on positioning or scope.
40–50%: weak PMF — keep iterating, watch the retention curve.
50%+: strong PMF — can think about Launch stage.

## False positives — what looks like PMF but isn't

Before celebrating any number, ask: *what would a skeptic say about this?*

| Number that looks good | Skeptic's question | How we test |
|---|---|---|
| High D1 retention | Were they founder's friends? | Look at signups by source; exclude `referrer:tom` |
| High activation | Did we DM them through it? | Pull manual outreach log |
| "Very disappointed" >40% | Asked too early before genuine usage? | Require ≥5 sessions before the survey counts |
| Lots of trades placed | Are they all the same user? | Per-user trade count distribution |
| Lots of pinned widgets | Are they all morning-briefs? | Diversity of pinned widget types |

We track these counter-metrics explicitly — they appear next to the positives on the dashboard.

## PostHog events to instrument

These fire from the frontend (PostHog browser SDK) and/or backend (PostHog Python SDK).

### Account lifecycle

- `user_signed_up` — properties: `source`, `signup_method`
- `user_returned` — properties: `days_since_first_seen`, `session_number`
- `user_churned` — derived: no event for 14 days

### Activation funnel

- `chat_session_started`
- `prompt_submitted` — properties: `intent_classification` (morning-brief / research / trade / etc.), `text_hash` (NEVER raw text — PII)
- `widget_generated` — properties: `widget_type`, `latency_ms`
- `widget_pinned` — properties: `widget_type`, `time_since_session_start_ms`
- `widget_unpinned` — properties: `widget_type`, `time_since_pin_ms`

### Trade flow

- `order_ticket_shown` — properties: `ticker`, `notional`, `has_tp_sl`
- `order_confirmed` — properties: `ticker`, `notional`, `from_research_card` (boolean)
- `order_cancelled`
- `live_trade_pinned`
- `trade_monitor_opened`

### Diagnostics

- `chat_error` — properties: `error_type`, `tool_name?`
- `widget_validation_failed` — properties: `widget_type`, `reason` (means the LLM produced invalid JSON — important quality signal)

## Weekly synthesis

Every Monday, run the PostHog dashboard plus this synthesis questions:

1. Which 3 users are most engaged? What do they have in common?
2. Which 3 users churned? Why?
3. Did any widget type get pinned ≥3× more than others?
4. Did any prompt intent fail to render a widget ≥20% of the time?
5. Median session length trend — going up, flat, or down?

Skeptic's audit:
- Of the active users, how many are founder's friends?
- Of the trades placed, what % are from a single user?
- Are we still hand-prompting users to come back? If so, retention isn't organic yet.

## When to call PMF

This is a judgement call combining the numbers with the lived signal. The MVP guide's "effort test" applies: *if the product is pulling users back without effort, that's PMF.* If we're still pushing — DMs, scheduled emails, manual outreach — we don't have it yet.

We don't call PMF before:
- 3+ iteration cycles
- ≥30 active weekly users
- Sean Ellis ≥40% on a non-DM-pressured sample
- D30 retention ≥20%

And even then, two more weeks of holding-pattern data before declaring.

## When to pivot

After 3 iteration cycles without movement toward these targets, run the diagnostic from the MVP guide:

1. Is there a segment in this data responding differently than the rest?
2. Is the gap between designed value and experienced value a positioning problem or a product problem?
3. What would have to be true for the current product to find PMF, and is that scenario realistic given what we're seeing?

If the answer to #3 is "we'd need to add 4 major features," that's a pivot signal — not a build signal.
