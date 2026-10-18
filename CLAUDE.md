# ArcumAI: working notes for Claude Code

Privacy-first AI assistant for Swiss legal and fiduciary offices. Open source (MIT). The owner is Nicolas; he decides priorities, approves design changes and merges.

## Layout

- `main_nice.py`: entry point (FastAPI + NiceGUI web app, Outlook WebSocket bridge at `/ws/outlook/{user_id}`)
- `src/config.py`: environment variables to typed constants (range-checked at startup); `src/auth.py`: users and login
- `src/engine.py`, `src/ai/`: hybrid RAG (LlamaIndex, ChromaDB, BM25), user sessions, PII masking (`ner_masking.py`), prompt optimizer
- `src/bridge/`: Outlook bridge (manager, loopback queue and processor, pending results), JSON-RPC over WebSocket
- `src/ui/`: NiceGUI components; `src/readers.py`: document readers (PDF, DOCX, MSG, EML, XLSX, TXT)
- `ingest.py`, `watcher.py`, `admin_tool.py`, `rag_query.py`: ingestion and tooling
- `outlook-plugin/ArcumAI.Outlook/`: C# VSTO add-in (Windows only)
- `tests/`: pytest suite; `doc/`: analyses and refactoring history; `CHANGELOG.md`: Keep a Changelog

## Commands

- Tests (always with the dev environment, as in CI), in PowerShell: `$env:ARCUMAI_ENV = "dev"; .venv\Scripts\python -m pytest tests/ -q`
- CI (`.github/workflows/tests.yml`) runs on Windows: pytest plus an msbuild compile of the Outlook add-in. The add-in cannot be compiled or tested outside Windows, so for C# changes the CI result is the only evidence.

## Rules

1. **Smallest change that works.** Preserve working logic, interfaces and architecture. No refactoring, renaming or "cleanup" that the issue does not ask for.
2. **Ask before** changing working logic, architectural decisions, public interfaces (including the plugin JSON-RPC protocol), adding dependencies, or anything breaking. Explain the change and the alternatives in the issue and wait for the owner's approval.
3. **Privacy is absolute.** Never read, print, log, copy or commit: `.env`, `users.json`, `input_utente/`, `data_*/`, `chroma_db/`, `storage/`, `storage_bm25/`, `logs/`, `debug/`, `temp/`, `prove/`. Use only synthetic data in tests, issues, commits and pull requests. Never add personal data to log output.
4. **Security-sensitive areas** (`src/auth.py`, session secrets, `src/ai/ner_masking.py`, static file mounts, the WebSocket bridge): extra care, and say explicitly in the pull request what was and was not verified.
5. **Evidence.** Never say that code works, passes tests or is secure unless you ran it and saw it. Report the command and the result. Separate confirmed defects from suspicions and optional improvements.
6. **Changelog.** Every user-visible change adds a line under `[Unreleased]` in `CHANGELOG.md`.
7. **Language.** Everything written into the repository or GitHub is in English: code, comments, identifiers, log and error messages, tests, docs, commit messages, branch names, issues, pull requests and review comments. Existing localized user-facing strings (Italian, German, French UI texts, trigger files) stay as they are unless an issue changes them. Conversation with the owner follows the owner's language. Never use em dashes.
8. **Attribution.** Every agent signs what it writes with its role name: `agent-product-owner`, `agent-architect`, `agent-tester` or `agent-developer`. Issue bodies, issue comments, pull request descriptions and pull request comments end with a last line containing only that name. Commit messages end with the trailer `Co-Authored-By: agent-<role>` (for example `Co-Authored-By: agent-developer`). No other attribution: no "Generated with" footer, no tool or product name.
9. **Shell.** ArcumAI is developed on Windows, so every command you run, or write into docs, scripts, issues and pull requests, must work in PowerShell. No `&&` or `||` chaining (Windows PowerShell 5.1 lacks them): use separate commands or `;`, and check `$LASTEXITCODE` after a native command that must succeed. No `VAR=value command` prefixes: use `$env:VAR = "value"`. No heredocs: pass multi-line text to `gh` and `git` with a PowerShell here-string (`@'` ... `'@`), `--body-file`, `git commit -F`, or several `-m` options. No Unix tools (`grep`, `sed`, `awk`, `head`, `tail`, `cat`, `ls -la`, `rm -rf`, `touch`): use `Select-String`, `Get-Content`, `Get-ChildItem`, `Remove-Item`, `New-Item`, or the dedicated file tools. If your shell tool is Git Bash (the owner's setup), run PowerShell commands through `powershell.exe -NoProfile -Command`, and check the result, because Git Bash treats quotes, `$` and backslashes differently. Use the project interpreter `.venv\Scripts\python`, and backslashes in local paths (forward slashes are fine inside `git` and `gh` arguments).

## Workflow (backlog = GitHub Issues of this repository)

Agents live in `.claude/agents/` (private repository, not part of this one): `product-owner`, `architect`, `developer`, `tester`. The main session coordinates them; none of them merges.

1. Owner gives a goal. `product-owner` writes an issue (user story, testable acceptance criteria, out of scope, privacy and security notes, proposed priority) with label `status:proposed`.
2. **Owner approves** by changing the label to `status:approved`.
3. `architect` reads the code and posts a design comment on the issue, then sets `status:designed`. If the design touches working logic, interfaces, dependencies or architecture, **the owner approves it in a comment** before any code is written.
4. `tester` turns the acceptance criteria into failing tests under `tests/` on the issue branch.
5. `developer` implements on branch `feat/<n>-<slug>` or `fix/<n>-<slug>`, makes the tests pass, updates the changelog, opens a pull request containing `Closes #<n>`, and sets `status:in-test`.
6. `tester` verifies the pull request against the acceptance criteria and reports on the pull request. CI must be green.
7. **Owner reviews and merges.** Nobody pushes to `main`, force-pushes, or merges.

Labels: `status:proposed`, `status:approved`, `status:designed`, `status:in-development`, `status:in-test`.
