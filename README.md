# HTF Test Bot — does higher-timeframe alignment actually help?

This is a SECOND, separate bot. It runs two versions of your MODIFIED strategy
side by side, live:

- **MOD**      = your current MODIFIED-COC (trending filter + double-confirm + R:R 2)
- **MOD_HTF**  = the same thing PLUS a higher-timeframe alignment filter
                 (only take a long if price is also above the 200-EMA, and a
                 short only if below it)

Backtest hinted MOD_HTF has slightly lower drawdown and slightly higher profit
factor in 2 of 3 years — but it was a small, mixed improvement. This bot settles
it with live, out-of-sample data instead of guessing.

Everything is the same robust setup as your first bot: runs hourly in the cloud,
backfills missed hours, multi-source data (no geo-block), Telegram alerts.

────────────────────────────────────────────────────────────────────────────
IMPORTANT: put this in a NEW repository
────────────────────────────────────────────────────────────────────────────
Do NOT overwrite your existing paper-bot repo. That one (ORIGINAL vs MODIFIED)
keeps running untouched. This is a separate experiment in its own repo.

────────────────────────────────────────────────────────────────────────────
SETUP (~10 min, same as before)
────────────────────────────────────────────────────────────────────────────
1. Create a NEW private repo, e.g. "htf-bot" (tick "Add a README file").

2. Upload:  paper_htf.py  and  requirements.txt

3. Create the workflow file: "Add file" → "Create new file" →
   name it exactly:  .github/workflows/paper.yml
   → paste the contents of paper.yml → commit.

4. Settings → Actions → General → Workflow permissions →
   "Read and write permissions" → Save.

5. (Optional Telegram) Settings → Secrets and variables → Actions →
   add the SAME two secrets as your other bot:
       TELEGRAM_TOKEN   and   TELEGRAM_CHAT
   (Messages will be tagged "MOD" / "MOD_HTF" so you can tell the bots apart.
    If you'd rather keep this bot silent, just skip the secrets.)

6. Actions tab → enable workflows → "htf-test-bot" → "Run workflow".
   Green tick = live. Then open STATUS.txt to see the scoreboard.

Note: this bot runs at :15 past each hour (your other bot runs at :05), so they
don't collide.

────────────────────────────────────────────────────────────────────────────
HOW TO READ IT
────────────────────────────────────────────────────────────────────────────
STATUS.txt shows both lines, e.g.:
    MOD:     12 closed | win 50% | PF 1.9 | ... | equity $10,300
    MOD_HTF:  9 closed | win 55% | PF 2.1 | ... | equity $10,410

The question this bot answers: after 30+ closed trades each, does MOD_HTF have
a higher profit factor and/or lower drawdown than plain MOD? If yes, consistently,
HTF alignment is worth adding to your main strategy. If they're basically the
same, the backtest "improvement" was noise and you keep MOD as-is.

Same rules as always: don't touch the config, judge only at 30+ closed trades
per strategy, no real money involved.
