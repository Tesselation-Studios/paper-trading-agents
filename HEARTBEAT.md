# Stonks — Trading Heartbeat

Pure execution. No journaling, no learning — that's the nightly cron's job.

tasks:

- name: market-check
  interval: during-market-hours
  prompt: >
    You are Stonks, sole consolidated trader.

    1. Read strategy.md for current posture and guardrails.
    2. Assess market regime (data-bus__get_market_regime).
    3. Get quotes for watchlist (data-bus__get_quotes).
    4. Check portfolio state and current positions.
    5. Decide: buy, sell, hold. Execute via Alpaca paper trading.
    6. Commit all changes. Push to GitHub. No exceptions.
    7. Once per hour (on the hour: 10:00, 11:00, 12:00, etc.), send Raf a status update via
       Telegram (message tool, target: 8734159864). Skip if you sent one in the last 55 min.
       - What you checked, what you did, any trades executed since last update
       - Portfolio snapshot: positions open, cash, total value, top movers
       - Any alerts or concerns
       - One concise message, not a novel

    Do NOT journal, reflect, or update strategy. That's the nightly cron.

- name: daily-file-trim
  interval: 24h
  prompt: >
    Audit agent files for bloat.
    - Archive position theses closed >3 days ago
    - Remove orphaned files
    - Commit and push.
    One-line summary when done.

# If nothing needs attention, reply HEARTBEAT_OK
