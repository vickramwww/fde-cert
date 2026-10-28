# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The repo currently contains only [SPEC.md](SPEC.md) — a design spec for the "Smart Backlog Assistant". There is no code, `requirements.txt`, tests, or git history yet, so there are no build/lint/test commands to run. Treat SPEC.md as the source of truth and update it (or add a README, which the spec says should hold setup instructions) as the implementation lands.

## What is being built

A small Python tool (target: 200–500 LOC, scoped for ~8–12 hours) that turns meeting notes, requirement docs (text/PDF), and an existing backlog (JSON) into structured user stories with acceptance criteria, priority, and category, and flags duplicates / suggests grouping in existing backlogs. Output must be explainable and reviewable.

## Planned architecture (pipeline)

Input parser (text, PDF, JSON) → preprocessing/cleanup → AI analysis engine (LLM; OpenAI, Anthropic, or Hugging Face are all acceptable) → structured output formatter. Existing backlog data feeds into the AI analysis step alongside the parsed notes.

Intended entry point and usage (from the spec):

```bash
python app.py --notes sample_meeting_notes.txt --backlog backlog.json
```

Setup per the spec: create a venv, `pip install -r requirements.txt`, provide the AI API key via environment variable / env file (this repo uses OpenAI with the key in `OPEN_API_KEY`, loaded from `.env`).

## Constraints from the spec worth preserving

- **Output schema**: JSON with a top-level `summary` and `user_stories[]`, each having `title`, `description`, `acceptance_criteria[]`, `priority`, `category`. The model should return valid structured JSON, and the code should validate it.
- **Prompt design**: structured prompt with role/objective, input context, output format, constraints/quality checks, and good-vs-poor examples. Key constraint: no speculative requirements unsupported by the input; preserve technical context. Prompt iterations should be documented.
- **Error handling**: handle empty/invalid input, malformed JSON, unreadable files, missing API key (warn), and failed API calls (retry or graceful fallback message). Add simple logging of processing steps.
- **Testing**: validate against three realistic sample inputs — vague meeting notes, a formal prioritized requirements doc, and a backlog JSON with mixed tasks and duplicates — plus malformed-input edge cases. Review is largely manual against source material.
- **Deliverables** (the spec lists these as required): problem/use cases, architecture diagram, prompt design notes, testing approach, working code, README setup instructions, and a short reflection on the approach and improvements.
