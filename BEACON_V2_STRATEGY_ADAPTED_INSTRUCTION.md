# Beacon V2 Strategy Adaptation

## Compatibility Verdict

The proposed instruction is not safe to apply verbatim as Beacon V2's new global strategy definition.

Applied as-is, it would reduce Beacon V2 in several ways:

- It would collapse Beacon from a regime-aware multi-strategy engine into a mostly single-style trend system.
- It would override Beacon's existing portfolio and execution safeguards with narrower per-trade logic.
- It would introduce hedge behavior that is only valid for MT5-style FX execution, not for Binance Spot execution.
- It would make EMA50/EMA200 the dominant decision rule and weaken the current multi-timeframe and weighted-vote architecture.
- It would claim a news filter that Beacon does not currently implement with a real external event feed.

The instruction does contain useful ideas, but only as an additive execution module.

## Beacon V2 Principles To Preserve

Any upgrade must preserve these Beacon V2 behaviors:

- Keep the weighted multi-strategy engine as the primary directional signal source.
- Keep regime-aware behavior instead of forcing trend-following in every regime.
- Keep ML confirmation and professional MT5 analysis gates optional and additive.
- Keep execution-provider awareness: Binance Spot and MT5 cannot share the same hedge logic.
- Keep existing spread, slippage, cooldown, drawdown, VaR, ES, correlation, concurrency, and idempotency controls.
- Keep backtesting, telemetry, Telegram alerts, and dashboard support intact.

## What Can Be Upgraded Safely

These parts of the proposed instruction are compatible when adapted correctly:

- ATR-based stop, take-profit, trailing, and recovery thresholds.
- Session-aware filtering for MT5 professional analysis execution.
- Market-structure confirmation as an additional quality gate.
- Reversal confirmation before releasing a protective hedge.
- Controlled re-entry logic after a hedge exit.

## What Must Be Changed Before Adoption

The new instruction must be adapted with these restrictions:

1. Trend-following must become a regime-priority module, not the only strategy.
2. EMA50 and EMA200 should be higher-timeframe confirmation inputs, not the sole entry trigger.
3. Buy-side hedge recovery must be MT5-only and disabled for Binance Spot execution.
4. Hedge logic must be optional and feature-flagged, not mandatory for every BUY trade.
5. Hedge logic must respect Beacon's existing execution blocking, idempotency, and active-position caps.
6. News filtering must remain optional until an actual external macro-event feed is integrated.
7. Position limits such as one BUY, one hedge SELL, and one re-entry SELL must be interpreted per symbol cycle, not globally across the whole bot.
8. ATR logic must augment existing portfolio risk controls, not replace them.

## Beacon V2 Adapted Instruction

You are an expert quantitative developer and algorithmic trading engineer.

Enhance Beacon V2 with an FX-focused ATR recovery execution module that integrates with the existing Beacon architecture instead of replacing it.

### Core Architecture Rule

Beacon V2 already has:

- a weighted multi-strategy signal engine
- regime-aware strategy selection
- optional ML confirmation
- optional professional MT5 multi-timeframe execution gating
- portfolio-level and execution-level risk controls

Your task is to add new logic in a way that strengthens this architecture.

Do not downgrade Beacon into a single-strategy bot.

### Primary Objective

The enhancement must:

1. Preserve Beacon's current weighted multi-strategy directional engine as the primary signal source.
2. Add ATR-driven execution refinement for MT5 FX symbols.
3. Add optional BUY-side-only hedge recovery for MT5 execution cycles where recovery logic is enabled.
4. Add reversal confirmation before closing a protective SELL hedge.
5. Add trailing and controlled re-entry logic for hedge management.
6. Keep all existing Beacon risk controls active and authoritative.
7. Avoid any logic that reduces flexibility in range, breakout, or mixed-regime conditions.

### Directional Logic

Use Beacon's existing weighted strategy vote as the base direction.

- If Beacon's base engine vote is BUY or SELL, that remains the primary directional intent.
- Treat EMA50 versus EMA200 and market-structure state as confirmation layers.
- Do not require EMA50/EMA200 alignment as the only source of truth unless the feature is explicitly configured as a filter.

Directional confirmation inputs may include:

- EMA50 vs EMA200
- market structure state such as HH/HL or LH/LL
- existing higher-timeframe alignment from Beacon professional analysis
- existing VWAP and session filters when MT5 professional execution mode is enabled

### Regime Rule

Trend-following should be prioritized only when Beacon's detected regime is trend-like.

- In trend regimes, strengthen the weight of trend-following and continuation logic.
- In breakout regimes, keep breakout and momentum logic active.
- In range regimes, do not disable mean-reversion or range-aware logic.

### ATR Logic

Use ATR period 14 as an execution-layer volatility input.

ATR may be used for:

- stop-loss distance
- take-profit distance
- trailing-stop distance
- hedge trigger distance
- reversal confirmation threshold distance

ATR logic must not replace Beacon's existing spread, slippage, cooldown, drawdown, VaR, ES, or correlation controls.

### Entry Logic

BUY and SELL entries must still start from Beacon's existing weighted signal and quality gates.

Additional FX execution refinement may be applied as follows:

#### BUY refinement

- Prefer entries where base Beacon direction is BUY
- Favor pullbacks into EMA or structure support when available
- Require bullish confirmation candle only as an execution refinement layer, not as a replacement for Beacon's core signal engine

#### SELL refinement

- Prefer entries where base Beacon direction is SELL
- Allow standard Beacon execution for SELLs
- Do not apply BUY-side hedge recovery logic to SELL cycles

### ATR Stop And Target Logic

For FX execution refinement, ATR-based defaults may be used when tighter strategy-specific levels are not already defined.

Recommended starting defaults:

- stop_loss_multiplier = 1.5
- take_profit_multiplier = 2.0 or higher

These values must remain configurable.

### BUY-Side Hedge Recovery Module

This module is optional and must only activate when all of the following are true:

- execution provider is MT5
- symbol is an allowed FX recovery symbol
- recovery mode is enabled in settings
- base trade direction is BUY
- live or simulated execution mode supports paired hedge state tracking

This module must never run for Binance Spot execution.

#### Hedge Trigger

The hedge trigger should be ATR-based and configurable.

- hedge_trigger = entry_price - ATR x hedge_multiplier
- recommended hedge_multiplier range = 0.5 to 0.7

Rules:

- hedge must trigger before the base stop-loss
- hedge must not trigger beyond the stop-loss zone
- only one primary hedge per BUY trade cycle
- hedge creation must respect existing execution idempotency and active-position limits

#### Hedge Execution

When the trigger is hit, open one protective SELL hedge tied to the BUY trade cycle.

The hedge must be tracked as part of a structured trade cycle state machine, not as an unrelated new trade.

### Hedge Trailing Logic

When the SELL hedge is in profit:

- trailing_distance = ATR x trailing_multiplier
- recommended trailing_multiplier range = 0.3 to 0.5

Trailing may activate after the hedge gains roughly 0.3 ATR in unrealized profit.

### Reversal Confirmation Before Hedge Release

Before closing a profitable SELL hedge because price rebounds toward the original BUY thesis, Beacon must confirm that the rebound is real.

A bullish reversal confirmation should require confluence from Beacon-compatible signals such as:

1. market structure improvement
2. bullish impulse or engulfing confirmation
3. price reclaim above EMA50 or equivalent fast-trend recovery threshold
4. price extension at least 0.3 to 0.5 ATR above the hedge stress zone
5. optional confirmation from Beacon higher-timeframe vote when available

Decision rule:

- if reversal is strong, close the SELL hedge and allow the BUY thesis to continue
- if rebound is weak, keep the hedge active and wait for better confirmation

### Anti-Churn Logic

To avoid repeated hedge losses:

- after a hedge closes, do not immediately reopen another hedge
- require a cooldown of at least 1 to 2 candles
- require price to revisit the hedge zone
- require bearish structure to re-confirm before allowing another hedge event

### Re-Entry Logic

If a hedge exits through trailing protection and bearish continuation remains valid:

- allow one SELL re-entry per BUY recovery cycle
- require continuation confirmation from structure and trend filters
- do not permit unlimited re-entries

### Exit Logic

Close or reduce the BUY thesis when Beacon's broader execution logic invalidates it, such as:

- bearish higher-timeframe shift
- structure breakdown
- strong bearish momentum against the position
- Beacon execution quality gates turning against the trade

Close the SELL hedge when:

- hedge trailing stop is hit
- bullish reversal confirmation is satisfied
- broader execution state no longer justifies downside protection

### Risk Management

Preserve Beacon's existing risk framework as authoritative.

The new module may add these trade-cycle rules:

- risk per trade target of 1 to 2 percent as a configurable FX execution profile
- maximum of one primary BUY, one protective SELL hedge, and one SELL re-entry per symbol cycle
- no martingale
- no grid expansion of recovery orders

But do not remove or weaken existing Beacon controls such as:

- cooldowns
- spread and slippage guards
- MT5 active position caps
- daily loss kill switch
- position-size caps
- portfolio exposure caps
- correlation caps
- VaR and ES portfolio limits
- strict execution idempotency

### Filters

Use filters in this order:

1. Beacon execution and risk guards
2. provider-specific readiness checks
3. session filter for MT5 professional execution paths
4. optional structure and trend confirmation
5. optional external news-event filter if a real event feed exists

Do not claim a high-impact news filter unless a real news-calendar integration is implemented.

### Failsafe Rules

- If ATR is abnormally low, disable hedge logic or widen thresholds conservatively.
- If spread is too high, skip the trade using Beacon's existing spread guard.
- If the execution provider is not MT5, disable hedge recovery logic.
- If portfolio or drawdown guards are triggered, the new module must stand down immediately.

### Required Functional Areas

Implement the enhancement as modular components that fit Beacon's service layout.

Suggested function areas:

1. trend_confirmation_filter()
2. atr_execution_profile()
3. buy_execution_refinement()
4. sell_execution_refinement()
5. buy_hedge_trigger_mt5_only()
6. hedge_reversal_confirmation()
7. hedge_trailing_manager()
8. hedge_reentry_logic()
9. execution_risk_guard_adapter()
10. trade_cycle_exit_manager()

### Output Requirements

Use Python and fit the current Beacon V2 modular service architecture.

- preserve provider-aware behavior for Binance Spot and MT5
- keep code modular
- keep comments brief and useful
- keep structured logging for entries, hedges, reversals, exits, re-entries, and errors

### Bonus Features

The enhancement should remain compatible with these Beacon capabilities:

- backtesting engine
- live trading toggle
- Telegram alerts
- dashboard status and execution visibility
- queue-mode MT5 execution workflow

## Final Rule

If any new logic conflicts with Beacon's current multi-strategy execution, portfolio risk management, provider constraints, or operational safeguards, preserve Beacon's existing behavior and make the new module optional.

Do not reduce Beacon V2's execution strength in order to force the new instruction.