# Phase 10 — Reasoning trace (pre-registration)

Written and committed **before** any trace code exists. Deviations are
logged in `docs/engineering-log.md` before the step they change.

Goal (roadmap): a full trace of every retrieval and reasoning step behind
a decision, so a flagged decision can be audited, not just trusted.

## Decisions (owner, 2026-10-06)

- **Scope: every LLM node** — sentiment analyst, Portfolio Manager and
  explainer — plus the explainer's retrieval and the risk node's VaR
  inputs. Every decision becomes auditable, not only explained ones.
- **Audit view: command-line report**, `scripts/audit_decision.py <id>`.

## 1. What is traced, per decision

- **LLM calls** (one LangChain callback, no per-node code): exact prompt
  messages, raw response, parsed output, model, tokens, duration, error.
- **Retrievals:** method, window (dates), `as_of` cutoff, query terms,
  and each returned headline with its rank and full-text score.
- **Checks and rules:** the explainer's citation check (inputs, problems)
  and the risk node's VaR inputs (worst-loss days, budget arithmetic).
- **Never stored:** API keys or request headers.

## 2. Storage

Migration 009, `trace_steps`: decision_id, step index, parent step, node,
kind (`llm` / `retrieval` / `check` / `rule`), input JSONB, output JSONB,
model, input/output tokens, duration, error. Steps are collected in memory
during the graph run and written **in the same transaction as the
decision**, so no decision exists without its trace. Hand-written SQL.

## 3. Audit tool

`scripts/audit_decision.py <decision_id>` prints the chain from the trace
alone: inputs -> retrieval -> prompt -> response -> check -> final action.

## 4. Pre-registered checks

- **Completeness (pass/fail, must be 100%):** for every explanation,
  re-running the citation check from the trace alone (retrieved ids from
  the traced retrieval, cited ids from the traced model output)
  reproduces the stored `risk_explanations` row exactly.
- **Overhead vs a trace-off baseline:** added wall time and stored bytes
  per decision, measured on the same dry run with tracing off and on.
  Reported, no pass bar.
- **No secrets:** a test scans stored traces for API-key patterns; any
  match fails.

## 5. Cost

Build, tests and dry run: free (fake LLMs). One small real check: ~5
explanations traced for real, about $0.02, under a hard cap, from ~$0.40
left of the $5 budget.

## Done means

Code and tests; completeness at 100% on the dry run and the small real
run; overhead reported; then roadmap and homepage updated.
