"""Offline tests: parsing, validation and retry behaviour (no network, fake client)."""
import json
from types import SimpleNamespace

import pytest

from backlog_assistant.analyzer import AnalysisError, analyze, extract_json, validate
from backlog_assistant.formatter import to_markdown
from backlog_assistant.parsers import InputError, load_backlog, load_document

GOOD = {
    "summary": "s",
    "user_stories": [{"title": "t", "description": "d", "acceptance_criteria": ["a"],
                      "priority": "High", "category": "Auth"}],
}


class FakeClient:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), 0
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls += 1
        msg = SimpleNamespace(content=self.texts.pop(0), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


def test_empty_and_missing_files(tmp_path):
    (tmp_path / "e.txt").write_text("  \n ")
    with pytest.raises(InputError):
        load_document(str(tmp_path / "e.txt"))
    with pytest.raises(InputError):
        load_document(str(tmp_path / "nope.txt"))


def test_malformed_backlog(tmp_path):
    bad = tmp_path / "b.json"
    bad.write_text("{not json")
    with pytest.raises(InputError):
        load_backlog(str(bad))
    bad.write_text("[]")
    with pytest.raises(InputError):
        load_backlog(str(bad))


def test_backlog_skips_untitled_items(tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps([{"title": "ok"}, {"x": 1}, "plain string"]))
    assert [i["title"] for i in load_backlog(str(f))] == ["ok", "plain string"]


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": 1} done') == {"a": 1}


def test_validate_reports_problems():
    with pytest.raises(ValueError, match="priority"):
        validate({"summary": "s", "user_stories": [{**GOOD["user_stories"][0], "priority": "Urgent"}]})
    with pytest.raises(ValueError, match="missing 'category'"):
        validate({"summary": "s", "user_stories": [{"title": "t", "description": "d",
                  "acceptance_criteria": ["a"], "priority": "Low"}]})


def test_retry_on_malformed_then_success():
    client = FakeClient("not json at all", json.dumps(GOOD))
    assert analyze("notes", None, client=client) == GOOD
    assert client.calls == 2


def test_gives_up_after_max_attempts():
    with pytest.raises(AnalysisError):
        analyze("notes", None, client=FakeClient("x", "y", "z"))


def test_markdown_renders():
    assert "### 1. t" in to_markdown(GOOD)


# ---- additional coverage: API errors, API key, PDF, samples, schema edges, CLI, logging ----
import logging
from pathlib import Path

try:  # openai 3.x ships httpx2; older versions use httpx
    import httpx2 as httpx
except ImportError:
    import httpx
import openai

import app as cli

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class RaisingClient(FakeClient):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    def create(self, **kw):
        self.calls += 1
        raise self.exc


def _status_error(cls, code):
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return cls("boom", response=httpx.Response(code, request=req), body=None)


def test_missing_api_key_stops_with_clear_error(monkeypatch):
    monkeypatch.delenv("OPEN_API_KEY", raising=False)
    with pytest.raises(AnalysisError, match="OPEN_API_KEY"):
        analyze("notes", None)


@pytest.mark.parametrize("exc, match", [
    (_status_error(openai.AuthenticationError, 401), "Authentication"),
    (_status_error(openai.NotFoundError, 404), "not found"),
    (_status_error(openai.InternalServerError, 500), "500"),
    (openai.APIConnectionError(request=httpx.Request("POST", "https://x")), "reach"),
])
def test_api_failures_become_user_facing_errors(exc, match):
    client = RaisingClient(exc)
    with pytest.raises(AnalysisError, match=match):
        analyze("notes", None, client=client)
    assert client.calls == 1  # SDK-level retries handle transient errors; no retry loop here


def test_refusal_is_reported():
    msg = SimpleNamespace(content=None, refusal="no")
    client = FakeClient()
    client.create = lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])
    with pytest.raises(AnalysisError, match="declined"):
        analyze("notes", None, client=client)


def test_prompt_includes_notes_and_backlog_as_data():
    seen = {}
    client = FakeClient()

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(GOOD), refusal=None), finish_reason="stop")])

    client.create = create
    analyze("my notes", [{"title": "my item"}], client=client)
    user = seen["messages"][1]["content"]
    assert "<notes>\nmy notes" in user and "my item" in user
    assert "valid" in seen["messages"][0]["content"].lower()


def _minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)


def test_pdf_text_is_extracted(tmp_path):
    f = tmp_path / "req.pdf"
    f.write_bytes(_minimal_pdf("Must support SSO"))
    assert "Must support SSO" in load_document(str(f))


def test_corrupt_pdf_is_rejected(tmp_path):
    f = tmp_path / "bad.pdf"
    f.write_bytes(b"this is not a pdf")
    with pytest.raises(InputError):
        load_document(str(f))


def test_pdf_without_text_is_rejected(tmp_path):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(100, 100)
    f = tmp_path / "blank.pdf"
    with open(f, "wb") as fh:
        w.write(fh)
    with pytest.raises(InputError, match="empty"):
        load_document(str(f))


def test_sample_inputs_load():
    notes = load_document(str(SAMPLES / "sample_meeting_notes.txt"))
    reqs = load_document(str(SAMPLES / "requirements_doc.txt"))
    backlog = load_backlog(str(SAMPLES / "backlog.json"))
    assert "Okta" in notes and "P0" in reqs
    ids = {i["id"] for i in backlog}
    assert {"BL-101", "BL-102", "BL-103", "BL-104"} <= ids  # the seeded duplicate pairs


def test_backlog_accepts_wrapped_object(tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"tasks": [{"title": "x"}]}))
    assert load_backlog(str(f)) == [{"title": "x"}]


@pytest.mark.parametrize("mutation, match", [
    ({"summary": ""}, "summary"),
    ({"summary": None}, "summary"),
    ({"user_stories": "nope"}, "user_stories"),
    ({"user_stories": ["str"]}, "must be an object"),
    ({"user_stories": [{**GOOD["user_stories"][0], "acceptance_criteria": []}]}, "acceptance_criteria"),
    ({"user_stories": [{**GOOD["user_stories"][0], "acceptance_criteria": [1]}]}, "acceptance_criteria"),
])
def test_validate_schema_edge_cases(mutation, match):
    with pytest.raises(ValueError, match=match):
        validate({**GOOD, **mutation})


def test_validate_rejects_non_object():
    with pytest.raises(ValueError):
        validate([GOOD])


def test_cli_requires_an_input(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2


def test_cli_reports_input_errors_with_exit_code_1(capsys):
    assert cli.main(["--notes", "does_not_exist.txt"]) == 1
    assert "Error: File not found" in capsys.readouterr().err


def test_cli_end_to_end_with_stubbed_model(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "analyze", lambda notes, backlog, model=None: GOOD)
    out, md = tmp_path / "o.json", tmp_path / "r.md"
    rc = cli.main(["--notes", str(SAMPLES / "sample_meeting_notes.txt"),
                   "--backlog", str(SAMPLES / "backlog.json"), "--out", str(out), "--markdown", str(md)])
    assert rc == 0
    assert json.loads(out.read_text()) == GOOD
    assert "### 1. t" in md.read_text()


def test_processing_steps_are_logged(caplog, tmp_path):
    f = tmp_path / "n.txt"
    f.write_text("hello")
    with caplog.at_level(logging.INFO):
        load_document(str(f))
        analyze("hello", None, client=FakeClient(json.dumps(GOOD)))
    text = caplog.text
    assert "Loaded" in text and "Calling" in text


def _story(*criteria, title="t"):
    return {**GOOD["user_stories"][0], "title": title, "acceptance_criteria": list(criteria)}


def test_number_check_flags_invented_numbers():
    from backlog_assistant.analyzer import check_unsupported_numbers
    result = {**GOOD, "user_stories": [_story("Session lasts 30 days", "Handles 50,000 rows", title="Exports")]}
    warnings = check_unsupported_numbers(result, "users complain; export fails above 50k rows")
    assert len(warnings) == 1 and warnings[0]["numbers"] == ["30"]  # 50,000 == 50k is supported


def test_number_check_passes_supported_numbers():
    from backlog_assistant.analyzer import check_unsupported_numbers
    result = {**GOOD, "user_stories": [_story("Done before Nov 30", "Under 5 seconds, 200 line items")]}
    assert check_unsupported_numbers(result, "Nov 30 deadline; under 5 seconds for up to 200 line items") == []


def test_analyze_attaches_warnings_and_markdown_shows_them(caplog):
    bad = {**GOOD, "user_stories": [_story("Lasts 7 days")]}
    with caplog.at_level(logging.WARNING):
        result = analyze("users must stay signed in", None, client=FakeClient(json.dumps(bad)))
    assert result["warnings"][0]["numbers"] == ["7"]
    assert "invented detail" in caplog.text
    assert "Warnings" in to_markdown(result)


def test_analyze_has_no_warnings_key_when_clean():
    assert "warnings" not in analyze("notes", None, client=FakeClient(json.dumps(GOOD)))
