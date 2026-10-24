# ADR-0002: Delegation and verification of AI-generated work

- Status: accepted 2026-10-24
- Deciders: architect, product owner

## Context

The owner uses several AI models. The architect (Claude Opus) keeps context and
quality; other models implement well-bounded tasks. Some of them cannot access
the repository. Generated code must never be integrated on trust.

## Decision

Roles:

| Model | Role | Typical work |
| --- | --- | --- |
| Claude Opus (architect) | Architecture, contracts, critical algorithms, review, integration | Contracts, acceptance tests, briefs, desurvey/kriging/pit kernels |
| Second Claude account | Senior implementer with repository access | Larger modules on a branch, opens a PR |
| DeepSeek | Bulk implementer without repository access | Self-contained adapters, forms, generators, extra tests |
| ChatGPT | Independent verifier | Reference calculations, test design, maths review |
| Gemini | Long-context auditor and researcher | Whole-repo consistency audits, public competitor documentation |
| Grok | Second opinion | Alternative analysis on disputed choices |

Process for every delegated task:
1. The architect writes the interface (types/protocols, no implementation) and
   acceptance tests that fail now. These are protected.
2. The architect writes `docs/tasks/T-NNN.md` with TOML front matter
   (`allowed_paths`, `protected_paths`, `acceptance`) and a short body.
3. The owner forwards a short prompt to the implementing model; for models
   without repository access the prompt carries the needed file contents.
4. The submission lands on a branch (directly, or uploaded by the owner).
5. `tools/verify_submission.py T-NNN` must pass: scope, protection, stub scan
   and all quality gates. Only then does the architect review the risky parts.
6. Numerical kernels additionally need an independent reference result.

Tasks below roughly 80 lines of code are not delegated; the brief would cost
more than the code.

## Consequences

- No task reaches VERIFIED without a passing verification run in the same session.
- Dependency manifests and lock files are protected; adding a dependency is an
  architect decision with a licence check.
