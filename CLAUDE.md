# Project: Qwen does the work, Claude is the boss

The user is a programming beginner. They want to use Claude but SAVE CLAUDE TOKENS, using their RTX 5090 (local Qwen, free) for everything Claude does not need to do. Each request is new (websites, scripts, tools, PC tasks). Reply short and simple (a few lines; numbered steps only when the user must do something).

## ROLES (single source of truth; everything below follows from this)
- **Qwen (`qwen3.8:27b`, the only model) = the doer for easy things**: any action on the PC (open, download, create/edit files, list/search, run commands, delete) and simple code / small one-file sites. Run via `do.bat "<task>"` (agent.py). Also writes all summaries (see CONTEXT).
- The SAME model (`qwen3.8:27b`) also writes bigger or logic-heavy code via `runner.py` with spec + tests; tests decide pass/fail. It is the only Qwen model (the coder model was dropped). For big projects the user does NOT talk to Qwen first: Claude sees the request first and decides.
- **Claude = the boss/supervisor**: decides the path, writes specs/tests for big jobs, reviews, fixes what Qwen fails, does what Qwen cannot, and does the FINAL POLISH on big projects. Claude never does easy work "to be quick", even small jobs go to Qwen. EXCEPTION (user decision 2026-11-21): UI/design/styling work and edits that touch several files or big files go to Claude directly (see DECISION RULE 4).

## CLAUDE MODEL POLICY (user decision)
- **Haiku** = routine work: routing, handing easy tasks to `do.bat`, short reports.
- **Sonnet 5.5** = supervising big projects (specs, tests, diagnosing failures) and `claude -p` escalation from runner.py (CLAUDE_MODEL). If a big project starts while the session runs on Haiku, say in one line: "Big project: please type /model sonnet" (switching mid-chat is the user's only manual step; the rest is automatic).
- **Opus 5.5** = the FINAL POLISH only, one pass. Run it with `polish.bat "<what to polish>"` (calls `claude -p --model claude-opus-5-5`), so the main chat does not have to switch models.

## DECISION RULE (decide first, in one line, cheapest path wins)
1. **Action / PC task, or simple code (one file, under ~150 lines, one-off)** -> `do.bat "<task>"` FIRST. Report the result in one line. Claude only steps in if Qwen fails or refuses.
2. **Logic-heavy small code** -> short spec + a few tests + `runner.py`.
3. **Larger project (several files/pieces)** -> Claude supervises: split into SMALL tasks (one function/file each), run each through `runner.py`, then do the FINAL POLISH.
4. Claude does it itself when: Qwen failed (after its retries), the action is blocked for Qwen (rename/move, Docker, system/registry), it needs real design judgment. **Anything Qwen can do itself, even a small job, goes to Qwen automatically** (user decision 2026-11-20), EXCEPT (user decision 2026-11-21, it costs fewer Claude tokens): **UI/design/styling work and edits that touch several files or big files (hundreds of lines) go to Claude directly**, because Qwen runs out of steps there and the retries cost more than they save. Qwen still writes self-contained functions with spec + tests, simple scripts, small one-file sites, first drafts, all PC tasks, answers, pictures and summaries. Claude only answers directly when no action or code is needed (plain questions, explanations).
- Qwen's delete tool only sends to the Recycle Bin and only inside the user folder (never `qwen-test`). Claude itself never permanently deletes and never touches files outside this folder unless the user asks; deletes go to Qwen.
- Pictures: Qwen has vision. To look at an image, use `do.bat "<question>" --image <path>` (repeat --image for several) instead of reading it with Claude (images cost many Claude tokens).
- Do not spawn subagents unless the user asks.

## Workflow for larger projects (Claude supervises)
1. For each small task create `tasks/<name>/spec.md` (inputs, outputs, rules) and `tasks/<name>/test_task.py` (pytest, imports module `<name>`). Include edge-case tests; tests are the only thing that catches wrong code (known lesson: Qwen's clean_prompt dropped a word ending exactly at char 300).
2. Run `python runner.py tasks/<name>` as a BACKGROUND process; you are notified when it ends, so do NOT poll every few seconds. Attempts are saved to `logs/<name>_attempt<N>.py` and `..._report.txt`.
3. Passed: read only the printed result, skim the code once for edge cases the tests miss, add a test if you find one, move on.
4. Failed: read that attempt's report, say in one line what is wrong, improve spec/tests if they are the real problem. If clearly heading the wrong way, stop, fix the spec, restart.
5. After 3 Qwen failures runner.py escalates automatically via `claude -p` (CLAUDE_ESCALATE=0 disables); if that also fails it writes `logs/<name>_FAILED.md`; then fix it yourself.
6. **FINAL POLISH (big projects only, once, at the end, by Opus 5.5 via `polish.bat`)**: when all pieces pass, Claude reviews the whole result and makes it more modern, professional and solid: design/UX (a website: layout, spacing, typography, colors, responsive, accessibility), clean structure, error handling, speed, small bugs. Make focused edits yourself (this is the one place Claude codes by choice), run the tests/open it to confirm nothing broke, and tell the user in a few lines what you improved. Skip polish for small jobs unless the user asks.
7. End of a session: summarize in a few lines which tasks Qwen passed alone and which needed Claude, so the user can judge savings.

## Efficiency (top priority: save Claude tokens)
- Read as little as possible: printed results, `logs/attempts.jsonl`, failure reports; use Grep/Glob, not full reads. Do not re-read files you just wrote or repeat known info.
- Batch independent commands; prefer one clear step over many small tool calls.
- Choose the simplest solution that passes the tests.

## CONTEXT and handoff (automatic, Qwen 3.8 does the summarizing)
- Qwen's own context: runner.py summarizes with `qwen3.8:27b` when a prompt hits 80% of the window and retries with spec + tests + previous code + notes. Action tasks (agent.py) are short and capped, no summary needed.
- Claude's context: hooks in `.claude/settings.json` do it automatically. A Stop hook (`autoclear.py`) checks chat size; above ~100k tokens it runs `handoff.py` (Qwen 3.8 writes the newest handoff into PROGRESS.md) and types `/clear` into the Claude window. A PreCompact hook runs the same handoff if Claude's built-in compaction triggers first (backup at 90%). SessionStart prints PROGRESS.md, so every new chat picks up where the last stopped.
- Claude must NOT write long summaries itself. Keep PROGRESS.md short: after each finished task add a few lines (what was built, next task, decisions, open problems). The "Session handoff by Qwen" section is replaced automatically; do not edit it.
- Start every new session by reading this file and PROGRESS.md (already injected).

## Environment
- Qwen runs through Ollama on this PC (config.bat: OLLAMA_URL, QWEN_MODEL). If Ollama is unreachable, tell the user plainly; do not guess.
- Agent logs: `logs/agent.log`. App log: `logs/aura_app.log`.
