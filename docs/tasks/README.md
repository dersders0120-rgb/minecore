# Task briefs

Each delegated task has a brief `T-NNN.md`. The front matter is TOML between
`+++` lines and is read by `tools/verify_submission.py`. Keep the body under
60 lines: context, exact behaviour, edge cases, out of scope.

```markdown
+++
id = "T-NNN"
title = "Short imperative title"
owner = "claude-2"            # opus | claude-2 | deepseek | chatgpt | gemini | grok
milestone = "M2"
depends_on = ["T-MMM"]
allowed_paths = ["python/minecore/io/collar_*.py", "tests/python/io/test_collar_extra_*.py"]
protected_paths = ["tests/python/io/test_collar_acceptance.py", "python/minecore/io/contracts.py"]
acceptance = "uv run pytest tests/python/io/test_collar_acceptance.py -q"
+++

## Context
## Required behaviour
## Edge cases
## Out of scope
## Report back
Files changed, commands run with their real output, open questions.
```

Global protected paths (never changeable by a delegated task) are listed in
`tools/verify_submission.py` (`GLOBAL_PROTECTED`).

Research and verification tasks for models without repository access (ChatGPT,
Gemini, Grok) use the same front matter; their deliverable is a document or a
set of reference values that the architect commits.
