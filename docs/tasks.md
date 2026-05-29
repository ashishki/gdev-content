# gdev-content Tasks

Status: secondary portfolio asset
Last updated: 2026-05-29

This project is maintenance-only. It supports `gdev-agent` as a focused example
of structured LLM content generation, schema outputs, guardrails, evals, and
human approval.

## Active Tasks

### GC-001: Companion Case Alignment

Owner: codex
Priority: P1
Status: planned

Objective: |
  Keep README and docs aligned with `gdev-agent` as the primary portfolio case,
  while presenting this repo as a smaller prompt/workflow-quality companion.

Acceptance-Criteria:
  - README explains the pipeline, outputs, evals, guardrails, and approval flow.
  - Docs do not imply a separate active product line.
  - Any example content is sanitized or synthetic.

### GC-002: Stub-Mode Verification

Owner: codex
Priority: P1
Status: planned

Objective: |
  Verify that the no-key stub mode and eval harness still work for demo and
  interview purposes.

Acceptance-Criteria:
  - Documented stub command runs or failures are recorded as maintenance issues.
  - Eval command is documented and does not require live secrets by default.
  - No model/provider change is made without a separate task.

### GC-003: Archive-Stable Review

Owner: human + codex
Priority: P2
Status: planned

Objective: |
  Decide whether this repo should remain a companion asset, be archived, or have
  selected patterns extracted into AI Workflow Playbook.

Acceptance-Criteria:
  - Review records keep/archive/extract decision.
  - Extracted lessons cite concrete docs or code paths.
  - No broad feature roadmap is opened.
