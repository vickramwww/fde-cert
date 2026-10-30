"""AI analysis engine: calls OpenAI, extracts JSON, validates and retries."""
import json
import logging
import os
import re

from .prompt import SYSTEM_PROMPT, build_user_prompt

log = logging.getLogger("backlog.analyzer")

DEFAULT_MODEL = "gpt-4o"
PRIORITIES = {"High", "Medium", "Low"}
STORY_FIELDS = ("title", "description", "acceptance_criteria", "priority", "category")
MAX_ATTEMPTS = 3


class AnalysisError(Exception):
    """User-facing failure of the AI step."""


def extract_json(text: str) -> dict:
    """Parse a JSON object from model text, tolerating code fences / stray prose."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def validate(result: object) -> dict:
    """Check the output schema; raise ValueError listing every problem found."""
    errors = []
    if not isinstance(result, dict):
        raise ValueError("top level must be a JSON object")
    if not isinstance(result.get("summary"), str) or not result["summary"].strip():
        errors.append("'summary' must be a non-empty string")
    stories = result.get("user_stories")
    if not isinstance(stories, list):
        errors.append("'user_stories' must be a list")
        stories = []
    for i, s in enumerate(stories):
        if not isinstance(s, dict):
            errors.append(f"user_stories[{i}] must be an object")
            continue
        for f in STORY_FIELDS:
            if f not in s:
                errors.append(f"user_stories[{i}] missing '{f}'")
        ac = s.get("acceptance_criteria")
        if "acceptance_criteria" in s and (
            not isinstance(ac, list) or not ac or not all(isinstance(a, str) for a in ac)
        ):
            errors.append(f"user_stories[{i}].acceptance_criteria must be a non-empty list of strings")
        if "priority" in s and s["priority"] not in PRIORITIES:
            errors.append(f"user_stories[{i}].priority must be one of {sorted(PRIORITIES)}")
    if errors:
        raise ValueError("; ".join(errors))
    return result


_NUM = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?[kKmM]?\b")


def _numbers(text: str) -> set[str]:
    """Normalised numbers in text: '50,000' and '50k' both become '50000'."""
    found = set()
    for m in _NUM.findall(text):
        m = m.strip().lower().replace(",", "")
        mult = {"k": 1000, "m": 1000000}.get(m[-1], 1) if m[-1] in "km" else 1
        value = float(m.rstrip("km")) * mult
        found.add(str(int(value)) if value == int(value) else str(value))
    return found


def check_unsupported_numbers(result: dict, source_text: str) -> list[dict]:
    """Flag numbers in stories (title, description, criteria) that never appear in the input."""
    known = _numbers(source_text)
    warnings = []
    for s in result["user_stories"]:
        text = " ".join([s["title"], s["description"], *s["acceptance_criteria"]])
        extra = sorted(_numbers(text) - known)
        if extra:
            warnings.append({"story": s["title"], "numbers": extra,
                             "message": f"Numbers not found in the input: {', '.join(extra)} - verify or remove."})
    return warnings


def analyze(notes: str | None, backlog: list[dict] | None, model: str | None = None, client=None) -> dict:
    """Run the analysis; retries on API errors and on invalid/malformed output."""
    import openai

    if client is None:
        api_key = os.environ.get("OPEN_API_KEY")
        if not api_key:
            raise AnalysisError("OPEN_API_KEY is not set - add it to .env or your environment.")
        client = openai.OpenAI(api_key=api_key, max_retries=3)  # SDK retries 429/5xx with backoff
    model = model or os.environ.get("BACKLOG_MODEL", DEFAULT_MODEL)

    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(notes, backlog)}]
    last_error = "unknown"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        log.info("Calling %s (attempt %d/%d)", model, attempt, MAX_ATTEMPTS)
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, response_format={"type": "json_object"}
            )
        except openai.AuthenticationError as e:
            raise AnalysisError("Authentication failed - check OPEN_API_KEY.") from e
        except openai.NotFoundError as e:
            raise AnalysisError(f"Model '{model}' not found - set BACKLOG_MODEL or --model.") from e
        except openai.APIConnectionError as e:
            raise AnalysisError(f"Could not reach the OpenAI API: {e}") from e
        except openai.APIStatusError as e:
            raise AnalysisError(f"API request failed ({e.status_code}): {e.message}") from e

        choice = resp.choices[0]
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            raise AnalysisError("The model declined to process this input.")
        text = choice.message.content or ""
        try:
            result = validate(extract_json(text))
            
            warnings = check_unsupported_numbers(result, f"{notes or ''}\n{json.dumps(backlog or [])}")
            for w in warnings:
                log.warning("Possible invented detail in '%s': %s", w["story"], w["message"])
            if warnings:
                result["warnings"] = warnings
            return result
        except (json.JSONDecodeError, ValueError) as e:
            last_error = str(e)
            log.warning("Invalid model output (%s)%s", last_error,
                        " - truncated" if choice.finish_reason == "length" else "")
            # Feed the error back so the model can correct itself.
            messages = messages[:2] + [
                {"role": "assistant", "content": text or "{}"},
                {"role": "user", "content": f"That response was invalid: {last_error}. "
                                            "Return the complete corrected JSON object only."},
            ]
    raise AnalysisError(f"Model did not return valid structured output after {MAX_ATTEMPTS} attempts: {last_error}")
