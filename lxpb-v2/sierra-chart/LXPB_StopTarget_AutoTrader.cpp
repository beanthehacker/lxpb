// LXPB_StopTarget_AutoTrader.cpp
//
// Auto-trading ACSIL study implementing the "2pt stop / 8pt target on strong-
// breakout LXPB retests" strategy shown in exit_analysis_report.html (see
// D:\lxpb\lxpb-v2\analyze_breakout_exits.py's stop/target grid search --
// stop=2, target=8 was one of the best-performing pairs by avg_R at n>=20 on
// the 72-trade 2026-07-01..2026-08-31 strong-breakout ES sample).
//
// This is a from-scratch, SINGLE-CHART, minimal port -- deliberately NOT
// built on top of D:\SC\ACS_Source\LXPB_H1_1s.cpp (that file's FilterEntry()
// is a permanently-disabled stub, and its StopRun/Pattern/Auction/
// Accumulation filters were never part of what analyze_breakout_exits.py
// actually backtested, so trading off it would not reproduce the researched
// edge). Run this directly on an H1 (60-minute) chart of the instrument you
// want to trade (ES/MES, NQ/MNQ, etc.) with Trade Service connected.
//
// --- Strategy logic (mirrors D:\lxpb\lxpb.py's advance_one_bar exactly,
//     see that file's docstrings for the canonical reference) ---
//
//   Phase 1 (formation): every closed H1 bar's high becomes a candidate LHPB
//   ("Last High Pre-Breakout") level; every bar's low becomes a candidate
//   LLPB level. No spike/swing filtering (lxpb.py tracks those as descriptive
//   fields only -- they do NOT gate which levels can breakout/retest, and
//   analyze_breakout_exits.py's backtest didn't filter on them either).
//
//   Phase 2 (breakout): a candidate level is consumed on the first later bar
//   whose range reaches it -- either promoted (close breaks through, or the
//   bar gaps entirely past it) or discarded forever (range touched it but
//   close didn't confirm the break). Only STRONG breakouts are promoted to
//   an active/tradeable level here (breakout bar range >= BreakoutRatio x
//   the average range of the AvgRangeWindow bars strictly before it) -- this
//   restriction is what analyze_breakout_exits.py's grid search actually
//   measured (the "72 strong-breakout trades" sample), so it's ON by default
//   (StrongBreakoutOnly) to match the researched edge; non-strong breakouts
//   are simply dropped (matches lxpb.py's own core state machine otherwise,
//   which has no strength filter at all -- set StrongBreakoutOnly=No to
//   trade every breakout/retest instead).
//
//   Phase 3 (retest / cooldown): once broken out, a level must go
//   MinBarsBeforeRetest closed H1 bars (default 1, matching lxpb.py's
//   MIN_HOURS_BEFORE_RETEST=1) WITHOUT being touched before it's eligible to
//   trade -- elapsed bars must strictly EXCEED MinBarsBeforeRetest (not >=),
//   so the very next H1 bar after the breakout bar can never itself be the
//   retest; at least one full bar always sits in between. If price
//   touches/gaps over the level during that cooldown, the level is silently
//   discarded (no trade), exactly like lxpb.py. Bar-COUNT elapsed is used as
//   a conservative proxy for lxpb.py's real elapsed-hours check: H1 bars can
//   only ever span >= their count in real calendar hours (session/weekend
//   gaps only ADD calendar time), so requiring elapsed > MinBarsBeforeRetest
//   closed bars never arms earlier than the real >1-hour mark lxpb.py
//   enforces.
//
//   Execution once the cooldown bar closes untouched: a resting LIMIT entry
//   order is placed EXACTLY at the level price (Buy Limit for LHPB -- price
//   pulls back down into a broken-out former high -- Sell Limit for LLPB --
//   price rallies back up into a broken-out former low), with an attached
//   bracket (Target1Offset/Stop1Offset, OCO). This is a deliberate
//   improvement over a naive "detect at bar-close, send a market order"
//   port: analyze_breakout_exits.py's backtest assumed a fill exactly AT the
//   level (its bar-by-bar walk starts every trade at entry_price with zero
//   slippage) -- for a 2pt stop, several points of H1-bar-close slippage
//   would meaningfully change the edge, so a resting limit order (which
//   Sierra Chart's order-matching engine fills tick-accurately, in both live
//   trading and Chart Replay/backtesting, whenever price actually reaches
//   the level) reproduces the backtest's assumption far more faithfully. The
//   one edge case where this can't apply -- price already touching/gapping
//   past the level on the very bar the cooldown expires -- falls back to an
//   immediate MARKET entry (logged distinctly), since Sierra Chart wasn't
//   asked to watch for that level intrabar before that bar closed.
//
//   Only ONE position/working entry order at a time (AllowOverlappingPositions
//   input, default No) -- simpler and safer for real money than the
//   backtest's per-trade-in-isolation vectorized simulation, at the cost of
//   possibly skipping a retest if one is already in progress when another
//   qualifies. Set AllowOverlappingPositions=Yes to remove this restriction
//   (arms every qualifying level's resting order independently).
//
// --- Setup in Sierra Chart ---
//   1. Open an H1 (60-minute) chart for the instrument to trade.
//   2. Chart >> Studies... >> Add Custom Study >> "LXPB Stop/Target Auto-Trader".
//   3. Set inputs (Stop Points / Target Points default to the 2 / 8 backtest
//      pair; Strong Breakout Only defaults On to match exit_analysis_report.html).
//   4. Enable Trade >> Auto Trading Enabled - Global AND - Chart, and turn on
//      Trade >> Trade Simulation Mode On while testing.
//   5. Flip the study's "Strategy Enabled" input to Yes.
//
// --- Backtesting in Sierra Chart ---
//   Yes -- via Chart Replay (Chart >> Replay Chart), NOT a separate
//   vectorized backtester like analyze_breakout_exits.py. With Trade
//   Simulation Mode on and this study's orders routed through
//   SendOrdersToTradeService, replaying historical data ticks/bars through
//   the chart drives this same live-trading code path, and Sierra Chart's
//   order-matching engine fills the resting limit/stop/target orders exactly
//   as it would live. Enable "Accurate Trading System Back Test Mode" in the
//   Replay dialog for tick-accurate fills, then review results in
//   Trade >> Trade Activity Log / Trade >> Trade Statistics afterward. This
//   is slower than the Python grid search (it replays real time, at whatever
//   replay speed you set) but exercises the REAL order-submission code, so
//   it's the most trustworthy way to validate this exact .cpp before going
//   live. There's also Trade >> Automated Trading Bar-Based Backtesting,
//   which is faster but only evaluates bar-close conditions -- since this
//   strategy's fills are intrabar (resting limit orders), Chart Replay is
//   the more accurate of the two for THIS study.
//
#include "sierrachart.h"
#include <vector>

SCDLLName("LXPB_StopTarget_AutoTrader")

// ---------------------------------------------------------------------------
// Data structures
// ---------------------------------------------------------------------------

enum LevelType { LHPB = 0, LLPB = 1 };  // LHPB -> long entry, LLPB -> short entry

struct FormingLevel {
    LevelType type;
    float price;
    int formation_bar;
};

struct PendingLevel {
    LevelType type;
    float price;
    int breakout_bar;
    bool armed;  // resting entry order currently working for this level
};

struct LXPBTraderState {
    std::vector<FormingLevel> touch_lv0;  // formed, not yet broken out
    std::vector<PendingLevel> touch_lv1;  // broken out, awaiting retest
    int last_processed_bar = -1;
    // Identity of whichever level currently has a working resting order (or
    // an open position originating from one), so a fill can be matched back
    // to remove it from touch_lv1. -1 type = none armed right now.
    int armed_type = -1;
    float armed_price = 0.0f;
};

// ---------------------------------------------------------------------------
// Study
// ---------------------------------------------------------------------------

SCSFExport scsf_LXPB_StopTarget_AutoTrader(SCStudyInterfaceRef sc)
{
    SCInputRef i_Enabled          = sc.Input[0];
    SCInputRef i_Quantity         = sc.Input[1];
    SCInputRef i_StopPoints       = sc.Input[2];
    SCInputRef i_TargetPoints     = sc.Input[3];
    SCInputRef i_MinBarsRetest    = sc.Input[4];
    SCInputRef i_StrongOnly       = sc.Input[5];
    SCInputRef i_RatioThreshold   = sc.Input[6];
    SCInputRef i_AvgRangeWindow   = sc.Input[7];
    SCInputRef i_AllowOverlap     = sc.Input[8];

    SCSubgraphRef sg_BuySignal    = sc.Subgraph[0];
    SCSubgraphRef sg_SellSignal   = sc.Subgraph[1];
    SCSubgraphRef sg_ArmedLevel   = sc.Subgraph[2];

    if (sc.SetDefaults) {
        sc.GraphName  = "LXPB Stop/Target Auto-Trader";
        sc.StudyDescription =
            "Trades LXPB H1 strong-breakout retests with a fixed stop/target "
            "bracket (defaults: 2pt stop / 8pt target, matching "
            "exit_analysis_report.html). Run on an H1 (60-minute) chart.";
        sc.AutoLoop    = 1;
        sc.GraphRegion = 0;

        sc.AllowMultipleEntriesInSameDirection            = 0;
        sc.AllowOppositeEntryWithOpposingPositionOrOrders = 0;
        sc.CancelAllOrdersOnEntriesAndReversals           = 1;
        sc.SendOrdersToTradeService                       = 1;
        sc.SupportAttachedOrdersForTrading                = 1;
        sc.MaximumPositionAllowed                          = 1;

        i_Enabled.Name = "Strategy Enabled";
        i_Enabled.SetYesNo(0);

        i_Quantity.Name = "Order Quantity";
        i_Quantity.SetInt(1);

        i_StopPoints.Name = "Stop Points";
        i_StopPoints.SetFloat(2.0f);

        i_TargetPoints.Name = "Target Points";
        i_TargetPoints.SetFloat(8.0f);

        i_MinBarsRetest.Name = "Min H1 Bars Before Retest (cooldown, elapsed must exceed this)";
        i_MinBarsRetest.SetInt(1);

        i_StrongOnly.Name = "Strong Breakout Only (match exit_analysis_report.html sample)";
        i_StrongOnly.SetYesNo(1);

        i_RatioThreshold.Name = "Strong Breakout Ratio Threshold (x avg range)";
        i_RatioThreshold.SetFloat(2.0f);

        i_AvgRangeWindow.Name = "Avg Range Window (bars, for strong-breakout baseline)";
        i_AvgRangeWindow.SetInt(20);

        i_AllowOverlap.Name = "Allow Overlapping Positions (No = one trade at a time)";
        i_AllowOverlap.SetYesNo(0);

        sg_BuySignal.Name         = "Buy Entry Armed/Filled";
        sg_BuySignal.DrawStyle    = DRAWSTYLE_ARROWUP;
        sg_BuySignal.PrimaryColor = RGB(0, 200, 0);
        sg_BuySignal.LineWidth    = 2;

        sg_SellSignal.Name         = "Sell Entry Armed/Filled";
        sg_SellSignal.DrawStyle    = DRAWSTYLE_ARROWDOWN;
        sg_SellSignal.PrimaryColor = RGB(200, 0, 0);
        sg_SellSignal.LineWidth    = 2;

        sg_ArmedLevel.Name         = "Armed Level Price";
        sg_ArmedLevel.DrawStyle    = DRAWSTYLE_DASH;
        sg_ArmedLevel.PrimaryColor = RGB(255, 200, 0);
        sg_ArmedLevel.LineWidth    = 1;

        return;
    }

    LXPBTraderState* state = reinterpret_cast<LXPBTraderState*>(sc.GetPersistentPointer(1));
    if (state == nullptr) {
        state = new LXPBTraderState();
        sc.SetPersistentPointer(1, state);
    }

    if (sc.LastCallToFunction) {
        delete state;
        sc.SetPersistentPointer(1, nullptr);
        return;
    }

    if (!i_Enabled.GetYesNo())
        return;

    const int i = sc.Index;

    // Only run the (formation/breakout/cooldown) state machine once per
    // newly CLOSED bar -- AutoLoop may call us repeatedly for the still-
    // forming last bar on every tick, and historical bars only need
    // processing once.
    bool is_new_closed_bar = (i > state->last_processed_bar) &&
                              (sc.GetBarHasClosedStatus(i) == BHCS_BAR_HAS_CLOSED);

    int min_bars      = i_MinBarsRetest.GetInt();
    bool strong_only  = (i_StrongOnly.GetYesNo() != 0);
    float ratio_thr   = i_RatioThreshold.GetFloat();
    int avg_window    = i_AvgRangeWindow.GetInt();
    bool allow_overlap = (i_AllowOverlap.GetYesNo() != 0);

    if (is_new_closed_bar) {
        state->last_processed_bar = i;

        // --- Phase 3: cooldown / retest check for levels awaiting retest ---
        // (armed levels with a working resting order are left alone here --
        // Sierra Chart's own order engine handles their fill intrabar.)
        std::vector<PendingLevel> keep_lv1;
        keep_lv1.reserve(state->touch_lv1.size());
        bool armed_one_this_bar = false;

        for (size_t k = 0; k < state->touch_lv1.size(); k++) {
            PendingLevel lv = state->touch_lv1[k];

            if (lv.armed) {
                keep_lv1.push_back(lv);  // still working, leave as-is
                continue;
            }

            int elapsed = i - lv.breakout_bar;
            bool touched = (sc.Low[i] <= lv.price && lv.price <= sc.High[i]);
            bool gap_over = (lv.type == LHPB) ? (sc.High[i] < lv.price)
                                               : (sc.Low[i] > lv.price);
            bool interaction = touched || gap_over;

            if (interaction) {
                if (elapsed > min_bars) {
                    // Cooldown already satisfied AND this very closed bar
                    // touched/gapped the level -- we only just found out at
                    // bar-close, too late for a resting order to have caught
                    // the exact touch, so fall back to an immediate market
                    // entry (only if we're allowed to / currently flat).
                    s_SCPositionData pos;
                    sc.GetTradePosition(pos);
                    bool can_trade = allow_overlap ||
                                      (pos.PositionQuantity == 0 && pos.WorkingOrdersExist == 0);
                    if (can_trade) {
                        s_SCNewOrder order;
                        order.OrderType         = SCT_ORDERTYPE_MARKET;
                        order.OrderQuantity      = i_Quantity.GetInt();
                        order.TimeInForce        = SCT_TIF_GOOD_TILL_CANCELED;
                        order.Target1Offset      = i_TargetPoints.GetFloat();
                        order.Stop1Offset        = i_StopPoints.GetFloat();
                        order.AttachedOrderTarget1Type = SCT_ORDERTYPE_LIMIT;
                        order.AttachedOrderStop1Type   = SCT_ORDERTYPE_STOP;

                        SCString msg;
                        if (lv.type == LHPB) {
                            sg_BuySignal[i] = sc.Low[i];
                            sc.BuyEntry(order);
                            msg.Format("LXPB MARKET BUY (late-detected retest) level=%.2f bars_since_breakout=%d",
                                       lv.price, elapsed);
                        } else {
                            sg_SellSignal[i] = sc.High[i];
                            sc.SellEntry(order);
                            msg.Format("LXPB MARKET SELL (late-detected retest) level=%.2f bars_since_breakout=%d",
                                       lv.price, elapsed);
                        }
                        sc.AddMessageToLog(msg, 0);
                    }
                    // Consumed either way (one-touch rule) -- drop from touch_lv1.
                } else {
                    // Touched/gapped before the cooldown elapsed -- silently
                    // discard, exactly like lxpb.py's Phase 3 "else" branch.
                    SCString msg;
                    msg.Format("LXPB level %.2f (%s) invalidated: touched at bar %d, only %d/%d bars since breakout",
                               lv.price, lv.type == LHPB ? "LHPB" : "LLPB", i, elapsed, min_bars);
                    sc.AddMessageToLog(msg, 0);
                }
                // Either branch: level consumed, do not keep.
                continue;
            }

            if (elapsed > min_bars) {
                // Cooldown satisfied and never touched during it -- arm a
                // resting limit entry order at the exact level price.
                bool can_arm = !armed_one_this_bar &&
                               (allow_overlap || state->armed_type == -1);
                if (can_arm) {
                    s_SCPositionData pos;
                    sc.GetTradePosition(pos);
                    bool can_trade = allow_overlap ||
                                      (pos.PositionQuantity == 0 && pos.WorkingOrdersExist == 0);
                    if (can_trade) {
                        s_SCNewOrder order;
                        order.OrderType         = SCT_ORDERTYPE_LIMIT;
                        order.Price1            = lv.price;
                        order.OrderQuantity      = i_Quantity.GetInt();
                        order.TimeInForce        = SCT_TIF_GOOD_TILL_CANCELED;
                        order.Target1Offset      = i_TargetPoints.GetFloat();
                        order.Stop1Offset        = i_StopPoints.GetFloat();
                        order.AttachedOrderTarget1Type = SCT_ORDERTYPE_LIMIT;
                        order.AttachedOrderStop1Type   = SCT_ORDERTYPE_STOP;

                        SCString msg;
                        if (lv.type == LHPB)
                            sc.BuyEntry(order);
                        else
                            sc.SellEntry(order);
                        msg.Format("LXPB ARMED %s LIMIT @ %.2f (stop=%.2f target=%.2f, breakout bar %d, cooldown satisfied at bar %d)",
                                   lv.type == LHPB ? "BUY" : "SELL", lv.price,
                                   i_StopPoints.GetFloat(), i_TargetPoints.GetFloat(),
                                   lv.breakout_bar, i);
                        sc.AddMessageToLog(msg, 0);

                        lv.armed = true;
                        state->armed_type = (int)lv.type;
                        state->armed_price = lv.price;
                        sg_ArmedLevel[i] = lv.price;
                        armed_one_this_bar = true;
                    }
                }
            }
            keep_lv1.push_back(lv);
        }
        state->touch_lv1 = keep_lv1;

        // --- Phase 2: breakout check for zero-touch levels ---
        std::vector<FormingLevel> keep_lv0;
        keep_lv0.reserve(state->touch_lv0.size());
        double avg_range = -1.0;
        if (i - avg_window >= 0) {
            double sum = 0.0;
            for (int k = i - avg_window; k < i; k++)
                sum += (sc.High[k] - sc.Low[k]);
            avg_range = sum / avg_window;
        }

        for (size_t k = 0; k < state->touch_lv0.size(); k++) {
            const FormingLevel& lv = state->touch_lv0[k];
            float price = lv.price;
            bool broke;
            bool no_interaction;

            if (lv.type == LHPB) {
                no_interaction = (sc.High[i] < price);
                broke = !no_interaction && ((sc.Low[i] > price) || (sc.Close[i] > price));
            } else {
                no_interaction = (sc.Low[i] > price);
                broke = !no_interaction && ((sc.High[i] < price) || (sc.Close[i] < price));
            }

            if (no_interaction) {
                keep_lv0.push_back(lv);
                continue;
            }
            if (!broke)
                continue;  // range touched it but close didn't confirm -- discard forever

            // Broken out -- apply the strong-breakout filter (see file header).
            bool strong = true;
            if (strong_only) {
                double range_ratio = (avg_range > 0.0)
                    ? (double)(sc.High[i] - sc.Low[i]) / avg_range
                    : 0.0;
                strong = (avg_range > 0.0) && (range_ratio >= ratio_thr);
            }
            if (strong) {
                PendingLevel pv;
                pv.type = lv.type;
                pv.price = price;
                pv.breakout_bar = i;
                pv.armed = false;
                state->touch_lv1.push_back(pv);
            }
            // else: breakout wasn't strong enough -- dropped (StrongBreakoutOnly).
        }
        state->touch_lv0 = keep_lv0;

        // --- Phase 1: register this bar's high/low as new candidate levels ---
        FormingLevel new_high{LHPB, (float)sc.High[i], i};
        FormingLevel new_low{LLPB, (float)sc.Low[i], i};
        state->touch_lv0.push_back(new_high);
        state->touch_lv0.push_back(new_low);
    }

    // --- Housekeeping: detect fill of the currently-armed level so it's
    // removed from touch_lv1 and another level can be armed once flat again.
    if (state->armed_type != -1) {
        s_SCPositionData pos;
        sc.GetTradePosition(pos);
        if (pos.PositionQuantity != 0) {
            // Filled -- remove the matching armed entry from touch_lv1.
            for (size_t k = 0; k < state->touch_lv1.size(); k++) {
                if (state->touch_lv1[k].armed &&
                    state->touch_lv1[k].type == (LevelType)state->armed_type &&
                    state->touch_lv1[k].price == state->armed_price) {
                    state->touch_lv1.erase(state->touch_lv1.begin() + k);
                    break;
                }
            }
            state->armed_type = -1;
        } else if (!pos.WorkingOrdersExist) {
            // No position and no working orders -- the resting order must
            // have been cancelled/rejected externally; un-arm so it can be
            // reconsidered (still respects its own price/cooldown state).
            for (size_t k = 0; k < state->touch_lv1.size(); k++) {
                if (state->touch_lv1[k].armed &&
                    state->touch_lv1[k].type == (LevelType)state->armed_type &&
                    state->touch_lv1[k].price == state->armed_price) {
                    state->touch_lv1[k].armed = false;
                    break;
                }
            }
            state->armed_type = -1;
        }
    }
}
