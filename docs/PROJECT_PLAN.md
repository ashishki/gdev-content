# gdev-content - Project Plan

Status: secondary portfolio asset
Role: structured LLM content pipeline showcase
Priority: P2

## Strategic Role

`gdev-content` complements `gdev-agent`. It demonstrates prompt/version
discipline, schema outputs, guardrails, evals, webhook integration, and human
approval around support-ticket content generation.

It should be presented as a focused prompt-engineering and workflow-quality
case, not as an active standalone product.

## Near-Term Roadmap

### P0 - Portfolio Linkage

- Add a short note connecting it to `gdev-agent` as a companion pipeline.
- Keep README focused on outputs, evals, and guardrails.

### P1 - Demo Cleanliness

- Ensure stub mode works without API keys.
- Keep eval command documented.
- Add one sanitized example ticket/output if missing.

### P2 - Archive Stable

- Do not build new features unless a real support/content workflow needs them.

## Development Tasks

- Maintenance-only.
- Keep prompt/eval artifacts clean and readable.
- Avoid broad AI-development process notes in the public-facing README.

## Stop Conditions

- Stop if new work duplicates `gdev-agent`.
