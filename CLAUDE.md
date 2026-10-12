# CLAUDE.md

This file provides Claude-Code-specific workflow guidance for this repository. It intentionally does **not** duplicate the repo's architecture/contract documentation — that lives in **[AGENTS.md](AGENTS.md)**, which is the canonical, cross-agent (Claude, Codex, or any other coding agent) source of truth for this repo's invariants. **Read AGENTS.md before making any non-trivial change here.** If something in this file and AGENTS.md ever conflict, AGENTS.md wins; fix this file.

## What this is

個人用台股投資分析與策略研究平台（不下單，只分析＋研究＋交易日誌＋財務規劃）。基本面分析邏輯以 本機規格 `spec/台股投資分析實戰框架.md` 為藍本 — 個股頁、評分表、篩選器條件都對映此框架的章節，改動這些功能前應先看對應章節。財務規劃／ETF 長期投資功能的規格書是 `spec/個人財務狀況與 ETF 長期投資平台開發.md`（本機限定）；Strategy Lab / Portfolio 的規格書是 `spec/Strategy Lab-Backtesting Engine-Portfolio Lab.md`（兩份 spec 都在 `.gitignore` 排除的 `spec/`，只在本機）。本機優先，另有私人 Railway 部署；兩份 SQLite 資料庫不自動同步。

## Repo navigation for Claude Code

- **Architecture, module map, data/API flow**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Research/portfolio contracts currently in force** (point-in-time rules, OOS evidence hierarchy, exact sleeve, allocation/netting, immutable versions, ...): [docs/RESEARCH_CONTRACTS.md](docs/RESEARCH_CONTRACTS.md)
- **What's done, what's usable, what's not started**: [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)
- **Documentation map and historical stage reports**: [docs/README.md](docs/README.md), then `docs/history/stage-*.md` for point-in-time evidence.
- **Verified Railway state and open deployment limitations**: [docs/railway-deployment.md](docs/railway-deployment.md).
- **Invariants an agent must never break**: [AGENTS.md](AGENTS.md) — read this first for anything touching research correctness, strategy/portfolio versioning, or the generic Portfolio Engine.
- **Datasource gotchas** (TPEx SSL, MOPS domain/Big5 encoding, FinMind cumulative cashflow, institutional-flow column traps, ...): check this session's memory (`twse-data-source-gotchas.md`) before touching `app/datasources/` — re-hitting an already-solved bug there wastes real time.

## Commands

```bash
# 本機啟動（每日資料檢查由行程內排程執行；目前時鐘格點為 15:00、15:15…）
uv run uvicorn app.main:app --port 8787

# 主要驗證：離線單元測試（不打外部 API）
uv run python -m unittest discover -s tests -p 'test_*.py'

# 煙霧測試 — 實際打資料源 API 或跑真實研究流程，非 mock
uv run python tests/smoke.py            # 全部（基本面資料源/輕量層/深度層/財務規劃/Backtest 核心）
uv run python tests/smoke.py sources    # 只測資料源（twse/tpex/finmind/mis_snapshot）
uv run python tests/smoke.py finance    # 財務規劃引擎（純數學，不打 API，可任意重跑）
uv run python tests/smoke.py backtest   # Backtest 核心手算交叉驗證（純數學，不打 API，可任意重跑）

# 語法/編譯檢查（沒有另外設定 linter 或型別檢查）
uv run python -m compileall app tests scripts
```

環境變數在 `.env`（見 `app/config.py:load_dotenv`，極簡自製 parser，不用 python-dotenv）：`FINMIND_TOKEN`、`FUGLE_API_KEY`、`FEE_DISCOUNT`、`TAX_RATE` 等，設定範例見 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)，完整設定以 `app/config.py` 為準，部署專用設定另見 `docs/railway-deployment.md`。

## Working in this repo

- **Before editing**: read the target service's module docstring and its `tests/test_*.py` counterpart. Several modules (esp. under `app/services/portfolio/` and `app/services/walkforward/`) document *why* a behavior is the way it is inline — that context usually changes what a "fix" should look like.
- **Before changing a research or portfolio contract** (anything with a version string like `portfolio-sleeve/h0/2`, `wf-freeze/f0.5/2`, `cross-strategy-sleeve/g/1`): find the Stage report and the pinned tests first (`docs/RESEARCH_CONTRACTS.md` indexes these). A behavior change to an existing contract version is very likely wrong — it usually needs a new version string instead, unless the user explicitly asked for a breaking change.
- **Do not bypass an immutable / point-in-time / OOS-separation / no-silent-fallback contract because a prompt seems to need it.** If satisfying a request would require breaking one of the invariants in `AGENTS.md`, stop and say so instead of finding a technically-compliant workaround.
- **After finishing a task**: run the test file(s) covering what you touched, and the full suite (`uv run python -m unittest discover -s tests -p 'test_*.py'`) before calling any research/portfolio change done. Run `compileall` on anything you edited.
- **If you changed architecture, a DB schema, an API contract, or a research/portfolio invariant**: update `AGENTS.md` and the relevant `docs/*.md` in the same change — don't leave it for later.

## Git Attribution Rules

- Always use the repository owner's existing Git identity.
- Never modify `git user.name` or `git user.email`.
- Do not add Claude, Cursor, Anthropic, OpenAI, Codex, ChatGPT, Genspark, or any AI assistant as a Git author, committer, or co-author.
- Never add AI-generated `Co-Authored-By` trailers.
- Preserve legitimate human contributors and human co-authors.
- AI agents may assist with git add, commit, and push, but attribution must remain with the actual human repository author.
- Before committing, verify `git config user.name` and `git config user.email`.
- Verify the owner identity from the existing repository Git configuration; do not copy personal email addresses into documentation.
