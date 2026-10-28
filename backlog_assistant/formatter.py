"""Output formatting: raw JSON plus a human-readable Markdown report."""


def to_markdown(result: dict) -> str:
    out = ["# Backlog Analysis", "", "## Summary", result["summary"], ""]
    order = {"High": 0, "Medium": 1, "Low": 2}
    stories = sorted(result["user_stories"], key=lambda s: order.get(s["priority"], 3))
    out.append(f"## User Stories ({len(stories)})")
    for i, s in enumerate(stories, 1):
        out += ["", f"### {i}. {s['title']}",
                f"**Priority:** {s['priority']} | **Category:** {s['category']}"
                + (f" | **Source:** {s['source']}" if s.get("source") else ""),
                "", s["description"], "", "**Acceptance criteria**"]
        out += [f"- {a}" for a in s["acceptance_criteria"]]
        if s.get("rationale"):
            out += ["", f"_Why:_ {s['rationale']}"]
        if s.get("related_backlog_ids"):
            out.append(f"_Related backlog items:_ {', '.join(map(str, s['related_backlog_ids']))}")
    review = result.get("backlog_review") or {}
    if review.get("duplicates"):
        out += ["", "## Possible Duplicates"]
        out += [f"- {' <-> '.join(map(str, d.get('items', [])))}: {d.get('reason', '')}" for d in review["duplicates"]]
    if review.get("groups"):
        out += ["", "## Suggested Groupings"]
        out += [f"- **{g.get('name')}**: {', '.join(map(str, g.get('items', [])))}" for g in review["groups"]]
    if review.get("priority_suggestions"):
        out += ["", "## Priority Suggestions for Existing Items"]
        out += [f"- {p.get('item')}: {p.get('suggested_priority')} - {p.get('reason', '')}"
                for p in review["priority_suggestions"]]
    if result.get("warnings"):
        out += ["", "## Warnings (possible invented details)"]
        out += [f"- {w['story']}: {w['message']}" for w in result["warnings"]]
    if result.get("open_questions"):
        out += ["", "## Open Questions (needs human review)"]
        out += [f"- {q}" for q in result["open_questions"]]
    return "\n".join(out) + "\n"
