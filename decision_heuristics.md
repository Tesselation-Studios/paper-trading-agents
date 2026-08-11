# Decision Heuristics — the specific rulebook, checked before reasoning

Read fresh every tick alongside `strategy.md`/`params.json` (`tick_prompt.md` step 1). `strategy.md` holds general philosophy; **this file holds the specific, situational rules** — each `Active` node has a concrete trigger and a `Recommended action` leaf that resolves cleanly to **BUY, SELL, or HOLD**. This is the operational rulebook Stan checks first when a position/candidate matches a known situation; `strategy.md`'s gestalt reasoning is what runs when nothing here cleanly matches.

Still a **prior, not a gate**: a clean match is a strong default Stan can override with a stated concrete reason (logged as `[tree:<node-id>]` in `active.md`/`--rationale` either way). A node can only exist here if its pattern already cleared real evidence (split-window Sharpe backtest, or it's already a mechanized `executor.py` gate) — this file restates and operationalizes proven judgment, it never originates new judgment (see `v4-spec.md`'s "no per-condition micro-branches or switchboards" design decision, respected here by construction: every node traces to evidence, none are invented fresh).

Two sections: **Active** (real leaf + sizing authority) and **Watch** (named, tracked, zero authority — a match is a cue for *extra* scrutiny in full reasoning, never a shortcut).

No in-file changelog — same convention as `strategy.md`: `git log decision_heuristics.md` plus the corresponding `proposals/*.md` entry is the audit trail for every status/tier/content change.

**Conviction tiers** (see `params.json: decision_tree.tier_promotion` for the numeric promotion bar): `probe` (smallest of `risk.probe_position_pct`% of portfolio or `risk.probe_max_dollars`, floor 1 share — redefined 2026-08-11 from a flat 1-3sh default, which was price-independent and landing the same "1 share" on a $1.50 stock and a $300 stock), `standard` (`risk.max_position_pct`), `high-conviction` (reach for `--play-type conviction`, `risk.conviction_play.position_size_pct` — read live, don't hardcode the number here). Use `python3 scripts/position_sizing.py --tier <tier> --price <price>` to convert any tier's % target into a suggested `--qty` — an optional calculator for the judgment call below, not a formula it replaces. For `risk/restraint` and `risk/exit` node types, tier is inverted: high tier means strongly trust the HOLD/SELL leaf, not size up a BUY — see each node's `Sizing guidance`.

---

## Active

### NODE gate0_unpriceable_v1

**Status**: active
**Type**: data-integrity
**Origin**: root-caused 2026-08-10 — `merge_discoveries.py`'s `TICKER_HEADER_RE` silently dropped price for 68-81% of the watchlist for a week before being fixed. Not a strategy.md rule; a pure data-quality screen.
**Trigger**: a watchlist candidate has no usable/current price despite the discovery pipeline reaching it.
**Recommended action**: HOLD (skip this candidate for this tick without spending reasoning on it).
**Conviction tier**: n/a (screen, not a sizing decision)
**Sizing guidance**: n/a
**Evidence**: 2026-08-03 through 2026-08-10, 68-81% of watchlist_candidates rows had `price IS NULL`. Root cause fixed same day (regex capture group). Kept as a node so a future, differently-shaped data gap still short-circuits fast instead of repeating the week-long misdiagnosis.
**Confidence (tracked)**: n/a (screen)
**Override note**: do NOT conclude the underlying ticker is illiquid or bad from a match here — verify via a direct quote before writing it off structurally (the 2026-08-05 misdiagnosis was exactly this: a parsing bug got mistaken for a data-quotability gap and nearly got "fixed" by adding a gate that would have suppressed good tickers). If the data looks stale rather than the ticker being genuinely bad, re-check directly before defaulting to HOLD.

---

### NODE peaked_pump_chase_v1

**Status**: active
**Type**: risk/restraint
**Origin**: split from `strategy.md` into this file 2026-08-10 (previously strategy.md v1.15's "Never chase a peaked pump" prose — the rule itself is unchanged, only where it's written).
**Trigger**: fresh MACDh flip AND RSI already extended (>65 bullish / <35 bearish) AND price has already run >1% in the flip direction.
**Recommended action**: HOLD (do not enter on this flip).
**Conviction tier**: high-conviction (restraint sense — high confidence in the HOLD, which manifests as a downward sizing cap on any entry attempted anyway, not an upward one)
**Sizing guidance**: if overridden (see below), cap the entry at `probe` tier regardless of what the underlying setup would otherwise justify, unless the confirming signal below is independently strong enough to justify `standard` tier on its own separate merits.
**Evidence**: motivated by RDDT (Jul 31, -22.66% in 18min, the pre-rule loss). Validated with zero false positives since: BFH/FLXS (Aug 3), EZRA (Aug 4), MOVE (Aug 5), ADIG (Aug 7). Hit definition for `tree_scorecard.py`: of decisions tagged `tree_action_taken: overridden`, hit = eventual `label_win=False` (the HOLD would have been right).
**Confidence (tracked)**: insufficient_data (n=0 tagged decisions as of ship — tagging starts at launch). Pre-tree evidence above supports a provisional high-conviction seed; first divergence once n≥10 defers to the tracked number.
**Override note**: may still BUY despite the match, graduated not a hard cutoff, given one additional independent confirming signal outside technicals (catalyst/fundamentals/congress/wiki/ml_signal) — state it explicitly in `--rationale`, tag `tree_action_taken: overridden`.

---

### NODE market_context_exit_v1

**Status**: active
**Type**: risk/exit
**Origin**: split from `strategy.md` into this file 2026-08-10 (previously strategy.md v1.16's "Market-context exit discipline").
**Trigger**: open **standard** position up >2% intraday AND SPY's own trend turns (RSI drops >8pts, or MACDh flips/declines meaningfully). REACTIVE ONLY. Exempt: open `conviction_play` or active `long_play` (their own daily thesis re-confirmation applies instead — see `index_anchor_thesis_break_v1` below for the index-anchor case specifically).
**Recommended action**: SELL (trim or close), regardless of the position's own signals.
**Conviction tier**: high-conviction (exit sense — high confidence in the SELL call)
**Sizing guidance**: n/a — this is a pure exit leaf, it doesn't size a new BUY. High tier means trust the SELL recommendation strongly, don't hold out for the position's own target once it fires.
**Evidence**: hardened strategy.md v1.16. Zero false positives across Aug 3, 4, 5, 7 sessions. Hit definition: hit = the SELL fired and the position's subsequent path (had it been held) would have given back the gain, OR the position was correctly exempted.
**Confidence (tracked)**: insufficient_data (n=0 as of ship, same bootstrap-seed treatment as above).
**Override note**: **never apply preemptively** — do not skip or downsize a *new* entry just because SPY looks weak right now (confirmed by the Aug 7 tick-replay: BLFS/BVS/ADIG all worked despite a bearish SPY bias). This node only fires on an *already-open* position. May decline to SELL with a stated concrete reason; log the override.

---

### NODE scale_in_blocked_v1

**Status**: active
**Type**: structural
**Origin**: confirmed live, not a strategy.md rule — Alpaca's paper-trading environment 403s any same-side scale-in on a position with an active protective stop.
**Trigger**: considering adding to an existing position that already carries a resting protective stop.
**Recommended action**: HOLD (don't plan on scaling in — size the *first* buy correctly for the conviction level you actually have instead).
**Conviction tier**: n/a — not a judgment call, an environment constraint
**Sizing guidance**: n/a
**Evidence**: confirmed permanent across 2+ sessions — 3 blocked Aug 4 (BBSI, TRIP, UTMD), 15/15 blocked Aug 5 (every position with an active stop).
**Confidence (tracked)**: n/a (structural fact, not scored)
**Override note**: n/a — this isn't a judgment call to override, it's what the broker actually does.

---

### NODE catalyst_liquidity_pointer_v1

**Status**: active
**Type**: mechanized-pointer (not a decision leaf — informational only, real enforcement happens in code)
**Origin**: already mechanically enforced — `executor.py`'s `gate_catalyst_liquidity`, `params.json: risk_guards.catalyst_liquidity_gate`.
**Trigger**: catalyst-led entry on a sub-$500M market-cap name.
**Recommended action**: n/a (not a leaf) — pass `--market-cap`/`--avg-dollar-volume` on the BUY call so the already-mechanized gate at step 9 has data to work with (it fails open, not blocking, without that data).
**Conviction tier**: n/a
**Sizing guidance**: n/a
**Evidence**: n/a (pointer to existing mechanized gate)
**Confidence (tracked)**: n/a
**Override note**: n/a — the real gate still runs regardless of this node; this just saves Stan from re-deriving "should I check liquidity here" from scratch.

---

### NODE conviction_play_anchor_v1

**Status**: active
**Type**: entry
**Origin**: split from `strategy.md` into this file 2026-08-10 (previously strategy.md's conviction-play eligibility anchor prose, 2026-08-03).
**Trigger**: `record_decision.py reconcile` output shows `agreement: true` AND `signal_count >= 3` directional signals AND `combined_confidence >= 0.60`, on a candidate where a real researched thesis exists (fundamentals/congress/wiki/momentum — not technicals alone).
**Recommended action**: BUY, `--play-type conviction`. `--thesis-claim`/`--thesis-invalidation` are hard-required regardless of this node.
**Conviction tier**: high-conviction (entry sense)
**Sizing guidance**: target 60-100% of `risk.conviction_play.position_size_pct` (read live from params.json), gated by `gate_position_size`/`gate_bankroll`/`gate_conviction_play` exactly as today — this node grants no new sizing authority, it's an anchor for judgment, not a formula.
**Evidence**: this IS the existing strategy.md anchor. Hit definition: eventual `label_win` on decisions tagged `play_type: conviction` AND this node id.
**Confidence (tracked)**: insufficient_data (n=0 tagged with this node id as of ship). Starts at `standard` tier, NOT bootstrap-seeded high-conviction — unlike the restraint/exit nodes above, this node's historical occurrences weren't cleanly tallied against false positives the same way. Starts conservative; earns high-conviction tier via its own tracked number.
**Override note**: may decline conviction sizing inside the anchor, or reach for it outside the anchor, with a stated reason either way.

---

### NODE index_anchor_entry_v1

**Status**: active
**Type**: entry
**Origin**: split from `strategy.md` into this file 2026-08-10 (previously strategy.md's Index-anchor conviction positions section).
**Trigger**: candidate is SPY, QQQ, DIA, or IWM only (broad, mega-cap-liquid, non-leveraged, non-inverse, non-sector/thematic index ETFs — no other tickers qualify for this node). No prolonged-downturn regime signal (`get_market_regime` not reading sustained bearish/distribution across multiple sessions) AND the ETF's own technicals (MACDh/RSI) not in a multi-session declining trend. Score with `record_decision.py reconcile` built from `regime`/`macro`/cross-index `technical` keys instead of single-company ones; clears the same `agreement: true` / `signal_count >= 3` / `combined_confidence >= 0.60` bar as `conviction_play_anchor_v1`.
**Recommended action**: BUY, `--play-type conviction`. State the regime read explicitly in `--thesis-claim`/`--prediction-reason`.
**Conviction tier**: high-conviction, with an explicit current-policy cap (see Sizing guidance) — this is a deliberate exception to the normal tier-earning path, not a demotion of the node's own evidence.
**Sizing guidance**: exempt from `risk.conviction_play.position_size_pct` on the upside — an index-anchor position sitting above that cap organically is not a breach (`check_stops()` does not trim it, `INDEX_ANCHOR_TICKERS` in `executor.py`). **Current policy (2026-08-10, Raf's direction): do not actively add to an index-anchor position beyond its current size.** When a new opportunity needs cash, use `index_anchor_reallocation_v1` (sell it down) instead of deploying fresh capital alongside it. Earmark 2 of the 5 conviction slots for index-anchors, never more than 3 — leave at least 2 free for individual-stock conviction picks.
**Evidence**: this is the existing strategy.md index-anchor eligibility rule. First live fire: SPY, 2026-08-10, 1:02 PM ET (technical 0.80, narrative 0.70).
**Confidence (tracked)**: insufficient_data (n=0 tagged with this node id as of ship).
**Override note**: exempt from `market_context_exit_v1` (no fast intraday SPY-fade trim) — evaluated on its own daily thesis re-confirmation instead, see `index_anchor_thesis_break_v1`.

---

### NODE index_anchor_thesis_break_v1

**Status**: active
**Type**: risk/exit
**Origin**: split from `strategy.md` into this file 2026-08-10 (previously strategy.md's Index-anchor "Thesis break" exit trigger).
**Trigger**: open index-anchor position (SPY/QQQ/DIA/IWM, `play_type: conviction`) AND `get_market_regime` shifts to a genuine sustained bearish/distribution read (more than one session), OR the ETF's own MACDh/RSI turns and stays down across multiple ticks. Routed through the daily thesis re-confirmation mechanics in `tick_prompt.md` step 6.
**Recommended action**: SELL (full or partial), `--verdict broken`, `--close-reason 'thesis broken: <condition>'`.
**Conviction tier**: high-conviction (exit sense)
**Sizing guidance**: n/a — pure exit leaf.
**Evidence**: this is the existing strategy.md index-anchor thesis-break rule; no live occurrence yet as of ship (first index-anchor position opened same day).
**Confidence (tracked)**: insufficient_data.
**Override note**: distinct from `index_anchor_reallocation_v1` below — a thesis break is a real regime/technical deterioration, not a cash-need-driven sell-down; don't conflate the two `--close-reason`s.

---

### NODE index_anchor_reallocation_v1

**Status**: active
**Type**: risk/exit (reallocation, not a thesis break)
**Origin**: split from `strategy.md` into this file 2026-08-10 (previously strategy.md's Index-anchor "Reallocation sell-down" trigger, v1.19).
**Trigger**: a new candidate this tick clears its own bar (standard/long/conviction) but cash is tight, AND an open index-anchor position is available to sell down.
**Recommended action**: SELL (partial), `--close-reason 'reallocation: funding <ticker> entry'`. Valid and routine, not a last resort — the anchor exists to be spent down into better ideas, and is the preferred cash source over fresh capital per `index_anchor_entry_v1`'s current sizing policy. Sequence it: submit the SELL before the dependent BUY in the same tick (`check_order()` re-fetches live account/position data fresh on every call, so the freed cash is visible to the BUY's `gate_cash` check).
**Conviction tier**: n/a — this is a cash-management move, not a conviction call on the index-anchor position itself.
**Sizing guidance**: n/a
**Evidence**: this is the existing strategy.md reallocation rule; no live occurrence yet as of ship.
**Confidence (tracked)**: n/a.
**Override note**: n/a.

---

### NODE catalyst_led_entry_v1

**Status**: active
**Type**: entry
**Origin**: `tick_prompt.md` step 8's catalyst-without-technical-confirmation rule ("A catalyst-led entry doesn't need a technical green light").
**Trigger**: a candidate has a real, specific (not generic/boilerplate) news headline with `|sentiment| >= 0.5`, priced in a reasonable band, not already extended >20% above its 20-day MA — even with flat or mildly negative MACDh.
**Recommended action**: BUY (standard). State explicitly in the rationale that this is a catalyst-led entry, not a technical one.
**Conviction tier**: standard (deliberately capped below high-conviction — single-signal by design; a catalyst alone doesn't clear the conviction-play anchor's `signal_count>=3` bar)
**Sizing guidance**: target more of the standard `max_position_pct` cap than a pure probe, but do not reach for `--play-type conviction` on this trigger alone.
**Evidence**: this is the existing tick_prompt.md rule, added specifically because "no mention of news/fundamentals/ML signal" catalyst-led setups were collapsing into a MACD/RSI/volume checklist despite the rule already existing in prose.
**Confidence (tracked)**: insufficient_data (n=0 tagged as of ship).
**Override note**: this is "permission to act," not an obligation — declining is fine, HOLD instead, with a stated reason.

---

## Watch (tracked, zero sizing/action authority)

### NODE catalyst_gap_risk_v0

**Status**: watch
**Type**: risk/gap
**Trigger (tentative, not yet promoted)**: sub-$500M catalyst-led candidate that CLEARS `gate_catalyst_liquidity` (adequate $ volume data) but the catalyst itself is extreme/violent (e.g. large pre-move run, thin float even with adequate $ volume) — a distinct failure mode from the mechanized liquidity gate above.
**Recommended action**: n/a (not a leaf). A match is a cue to apply *extra* scrutiny in full step-8 reasoning, not a verdict. Log `[tree:catalyst_gap_risk_v0 possible match, no fast-path applied]` so recurrences are trackable.
**Conviction tier**: probe (forced — watch nodes never authorize sizing up)
**Sizing guidance**: n/a
**Evidence**: VATE (Aug 10, -46.2% gap-through-stop, real $650M catalyst on a $165M-cap name with adequate liquidity data) is occurrence #1 for this specific mechanism. MBBC/BJDX/CNH are liquidity-shaped, already covered by `catalyst_liquidity_pointer_v1` — do not double-count them here. Needs 1-2 more occurrences before promotion to `active`. Given the stakes, tracked with elevated priority in `tasks/pending.md`.
**Confidence (tracked)**: n/a — watch tier, no action to score yet.
**Override note**: n/a.

---

### NODE deployment_pressure_override_v0

**Status**: watch
**Type**: risk/structural-reason
**Trigger (tentative)**: the stated reason for an entry is a structural need (sector diversification, deployment-pressure quota, "I need a candidate for X") rather than the candidate's own signals earning it.
**Recommended action**: n/a (not a leaf). Treat a quota-pressure rationale as itself a red flag worth naming explicitly in the rationale, not a reason to skip the entry outright.
**Conviction tier**: probe (forced)
**Sizing guidance**: n/a
**Evidence**: OLP (Jul 30) — Stan's own journal: "I entered for sector diversification... the real reason was 'I want to deploy'... the market saw through it in 40 minutes." MBBC (Aug 3) — Stan explicitly self-compared: "the same class of error as OLP." 2 occurrences, self-named by Stan but not yet hardened.
**Confidence (tracked)**: n/a.
**Override note**: n/a.

---

### NODE same_session_reentry_v0

**Status**: watch
**Type**: risk/re-entry
**Trigger (tentative)**: re-entering a ticker in the same session after a stop-out, reasoning "the stop was mechanical, not a thesis break."
**Recommended action**: n/a (not a leaf). Require an additional independent confirming signal (fresh MACDh reversal, sentiment, volume) beyond "thesis unchanged" before re-entering.
**Conviction tier**: probe (forced)
**Sizing guidance**: n/a
**Evidence**: CLIR (Aug 4) — stopped out -10% at 9:55am, re-entered 1:55pm on "thesis unchanged," MACDh flipped bearish within 50 minutes. Survived a full CHOPPY session Aug 5 without re-breaching (mixed evidence). Open in `tasks/pending.md`: "track second occurrence before hardening."
**Confidence (tracked)**: n/a.
**Override note**: n/a.

---

### NODE choppy_regime_scope_v0

**Status**: watch
**Type**: context/regime
**Trigger (tentative)**: index-level regime reads `mean_reversion` or `volatility_spike` (direction-ambiguous, the K-Means analog of the old HMM's CHOPPY — see `get_market_regime`) AND an individual candidate shows an independently confirmed setup (its own clean technical + catalyst/fundamental signal) not explained by the index read.
**Recommended action**: n/a (not a leaf). A match is a cue to apply extra scrutiny — don't auto-skip solely because the index regime is direction-ambiguous, but also don't treat this as license to override that gating without real name-specific evidence.
**Conviction tier**: probe (forced)
**Sizing guidance**: n/a
**Evidence**: recurring across 4+ tick-replay sessions (SOFI/RDDT/MARA Aug 3, BLFS/LINE/DXCM/BLBD Aug 7, all under the old HMM's CHOPPY label), explicitly flagged in `tasks/pending.md` (2026-08-03) as "needs more data," not yet strong enough for a live rule change. 2026-08-11: `get_market_regime` switched from the HMM (SUSTAINABLE/EXHAUSTED/CHOPPY) to a local K-Means classifier (momentum_bull/momentum_bear/mean_reversion/volatility_spike/low_vol_drift) — the confidence-formula calibration issue this line used to flag as "separate, ongoing" was fixed for the HMM and is now moot (that model is retired from the live path); re-accumulate evidence under the new labels before trusting the occurrence count above as still current.
**Confidence (tracked)**: n/a.
**Override note**: n/a.
