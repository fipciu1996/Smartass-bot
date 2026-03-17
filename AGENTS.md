# Repository Guidelines

## Project Structure & Module Organization
This repository is a flat Python codebase with the runnable bot in `main.py`. The bot uses Telegram long-polling, calls OpenRouter with function tools, executes read-only SQL on PostgreSQL, and sends the model's final reply back to Telegram. Prompt text is stored in `prompts/` so wording changes do not require Python edits. Local smoke tests live in `test_integration.py`. Runtime configuration examples are in `.env.example`, and the only required package is declared in `requirements.txt`. Keep `day1.ipynb` only as a reference for the simple polling flow.

## Build, Test, and Development Commands
Create and activate a local environment first:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run module smoke tests from the repository root:

```powershell
python test_integration.py
python -m py_compile main.py test_integration.py
```

Run the bot with `python main.py` after creating `.env` from `.env.example`.

## Coding Style & Naming Conventions
Use 4-space indentation, snake_case for functions and variables, and PascalCase for classes such as `TelegramClient`, `PostgresSqlRunner`, and `OpenRouterToolAgent`. Match the existing style: explicit docstrings, type hints on public methods, and small helpers. Keep the prompt, tool schema, DB runner, and SQL safety checks compact and deterministic. There is no committed formatter or linter config in this snapshot, so keep changes consistent with existing files.

## Testing Guidelines
This project currently relies on script-based smoke tests rather than `pytest`. Add new checks as `test_*.py` files or extend `test_integration.py` with deterministic examples. For date-sensitive logic, pass explicit dates like `date(2026, 3, 17)` so prompts stay reproducible. Any change to tool calling should be validated across prompt construction, tool schema, SQL validation, and the assistant -> tool -> assistant loop.

## Commit & Pull Request Guidelines
Git history is not available in this checkout, so no repository-specific commit convention could be sampled. Use short imperative subjects such as `Fix yesterday date parsing` or `Enable SQL tool calling`, and keep the body focused on behavior changes. Pull requests should include affected intents, sample input/output, related issue links, and documentation updates. If SQL or Telegram formatting changes, include a console snippet showing the generated query or final message.

## Security & Configuration Tips
Do not commit database, Telegram, or OpenRouter credentials. Keep generated SQL read-only, reject multi-statement output, and preserve schema-qualified access to `gym.*`. Treat the database runner as read-only infrastructure; any change that weakens SQL validation or transaction read-only guarantees should be treated as a security regression.
