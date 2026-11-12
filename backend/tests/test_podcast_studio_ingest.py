import hashlib
import importlib.util
import ipaddress
import json
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "skills/public/podcast-studio/scripts/podcast_studio.py"
if not MODULE_PATH.is_file():
    pytest.fail(f"podcast_studio module is missing: {MODULE_PATH}", pytrace=False)
SPEC = importlib.util.spec_from_file_location("podcast_studio", MODULE_PATH)
ps = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ps
SPEC.loader.exec_module(ps)


@pytest.fixture(autouse=True)
def network_tripwire(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("unexpected network call")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr("urllib.request.urlopen", blocked)
    FakeConnection.instances.clear()
    FakeConnection.responses = []
    try:
        import requests

        monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    except ImportError:
        pass


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def full_source(text="原文内容。", source_id="s1"):
    raw = text.encode("utf-8")
    return {
        "source_id": source_id,
        "kind": "markdown",
        "requested_url": None,
        "final_url": None,
        "mime": "text/markdown",
        "charset": "utf-8",
        "converter": "passthrough",
        "warnings": [],
        "text": text,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "extracted_text_sha256": sha(text),
    }


def write_sources(tmp_path, text="原文内容。"):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"schema_version": "podcast-studio.v1", "sources": [full_source(text)]}, ensure_ascii=False), encoding="utf-8")
    return path


def draft(profile, text="口播正文。"):
    source = full_source()
    if profile in {"read_original", "news_single"}:
        voices = {"host": "doubao:zh_male_m191_uranus_bigtts"}
        turns = [{"turn_id": "t0001", "role": "host", "text": text, "source_ids": ["s1"]}]
    else:
        voices = {
            "male": "doubao:zh_male_m191_uranus_bigtts",
            "female": "doubao:zh_female_vv_uranus_bigtts",
        }
        turns = [
            {"turn_id": "t0001", "role": "male", "text": text, "source_ids": ["s1"]},
            {"turn_id": "t0002", "role": "female", "text": "女方回应。", "source_ids": ["s1"]},
        ]
    return {
        "schema_version": "podcast-studio.v1",
        "profile": profile,
        "title": "测试播客",
        "sources": [{key: source[key] for key in ("source_id", "raw_sha256", "extracted_text_sha256")}],
        "voices": voices,
        "turns": turns,
    }


def normalize(tmp_path, profile, text="口播正文。"):
    return ps.normalize_script(draft(profile, text), write_sources(tmp_path))


def test_markdown_intake_prepare_offsets_code_and_verbatim(tmp_path):
    text = "# 标题\n\n独特句。\n\n```python\nprint('保留代码')\n```\n"
    source_path = tmp_path / "source.md"
    source_path.write_text(text, encoding="utf-8")
    source = ps.intake(str(source_path), tmp_path / "out")
    assert source["kind"] == "markdown"
    assert source["text"] == text
    script = ps.prepare_verbatim(tmp_path / "out" / "sources.json", tmp_path / "out")
    assert script["profile"] == "read_original"
    assert script["approval"] == {"required": False, "status": "not_required", "approved_sha256": None}
    chunks = [json.loads(line) for line in (tmp_path / "out" / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "".join(chunk["text"] for chunk in chunks) == text
    for chunk in chunks:
        assert chunk["text"] == text[chunk["start"] : chunk["end"]]
        assert chunk["text_sha256"] == sha(chunk["text"])
    assert ps.validate_script(tmp_path / "out" / "script.json", tmp_path / "out" / "sources.json")["valid"] is True


def test_verbatim_character_boundaries(tmp_path):
    source = full_source("字" * 100_000)
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"schema_version": "podcast-studio.v1", "sources": [source]}), encoding="utf-8")
    accepted = ps.prepare_verbatim(path, tmp_path / "accepted")
    assert sum(len(turn["text"]) for turn in accepted["turns"]) == 100_000
    source["text"] += "超"
    source["extracted_text_sha256"] = sha(source["text"])
    path.write_text(json.dumps({"schema_version": "podcast-studio.v1", "sources": [source]}), encoding="utf-8")
    with pytest.raises(ps.PodcastError, match="100,001|100,000"):
        ps.prepare_verbatim(path, tmp_path / "rejected")


def test_empty_text_rejected(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text(" \n\t", encoding="utf-8")
    with pytest.raises(ps.PodcastError, match="empty|空"):
        ps.intake(str(path), tmp_path / "out")


def test_local_html_resources_removed_before_conversion(tmp_path):
    path = tmp_path / "unsafe.html"
    path.write_text(
        """<html><head><link rel='stylesheet' href='https://example.com/x.css'><style>body{background:url(file:///etc/passwd)}</style></head>
        <body><h1>可见标题</h1><img src='http://127.0.0.1/private.png'><script>touch sentinel</script>
        <iframe src='./local.html'></iframe><a href='file:///tmp/x'>本地链接</a><p style='background:url(./x.png)'>正文</p></body></html>""",
        encoding="utf-8",
    )
    source = ps.intake(str(path), tmp_path / "out")
    assert "可见标题" in source["text"]
    assert "正文" in source["text"]
    assert "touch sentinel" not in source["text"]
    assert "127.0.0.1" not in source["text"]
    assert "file://" not in source["text"]
    assert source["warnings"]


def test_convert_html_bytes_is_pure_and_sanitized():
    text = ps.convert_html_bytes(
        b"<h1>Title</h1><img src='https://example.com/x.png'><script>bad()</script><p>Visible</p>",
        "https://example.com/start",
        "https://example.com/final",
        {"Content-Type": "text/html; charset=utf-8"},
    )
    assert "Title" in text and "Visible" in text
    assert "bad()" not in text and "x.png" not in text


def make_docx(path, text="DOCX 可见正文。"):
    document = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
    <w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"""
    content_types = """<?xml version='1.0' encoding='UTF-8'?>
    <Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>
    <Default Extension='xml' ContentType='application/xml'/>
    <Override PartName='/word/document.xml' ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'/>
    </Types>"""
    with zipfile.ZipFile(path, "w") as doc:
        doc.writestr("[Content_Types].xml", content_types)
        doc.writestr("word/document.xml", document)


def test_docx_dispatch_and_zip_limits(tmp_path):
    path = tmp_path / "source.docx"
    make_docx(path)
    source = ps.intake(str(path), tmp_path / "out")
    assert source["kind"] == "docx"
    assert "DOCX 可见正文" in source["text"]
    make_docx(tmp_path / "too-many.docx")
    infos = [zipfile.ZipInfo(f"x/{i}") for i in range(2049)]
    with pytest.raises(ps.PodcastError, match="2048"):
        ps.check_docx_zip_limits(infos)
    one = zipfile.ZipInfo("word/document.xml")
    one.file_size = 25 * 1024 * 1024 + 1
    with pytest.raises(ps.PodcastError, match="25 MiB"):
        ps.check_docx_zip_limits([one])
    parts = []
    for i in range(5):
        info = zipfile.ZipInfo(f"x/{i}")
        info.file_size = 21 * 1024 * 1024
        parts.append(info)
    with pytest.raises(ps.PodcastError, match="100 MiB"):
        ps.check_docx_zip_limits(parts)


def minimal_pdf(path, lines):
    stream = "BT /F1 11 Tf 50 750 Td " + " ".join(f"({line}) Tj 0 -18 Td" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream",
    ]
    raw = "%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(raw.encode("latin-1")))
        raw += f"{index} 0 obj\n{obj}\nendobj\n"
    xref = len(raw.encode("latin-1"))
    raw += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    raw += "".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:])
    raw += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(raw.encode("latin-1"))


def test_sparse_pdf_fails_before_conversion(tmp_path, monkeypatch):
    path = tmp_path / "sparse.pdf"
    minimal_pdf(path, ["tiny"])
    monkeypatch.setattr(ps, "_run_conversion", lambda *args: pytest.fail("conversion must not run"))
    with pytest.raises(ps.PodcastError, match="sparse|稀疏"):
        ps.intake(str(path), tmp_path / "out")


def test_pdf_fidelity_metadata_and_deleted_body(tmp_path, monkeypatch):
    lines = ["Visible paragraph one with enough text.", "Visible paragraph two with enough text."]
    path = tmp_path / "source.pdf"
    minimal_pdf(path, lines)

    def converted(input_path, output_path):
        Path(output_path).write_text("# source\n\n<!-- Page 1 -->\n\nVisible paragraph one with enough text.\n\nVisible paragraph two with enough text.\n", encoding="utf-8")

    monkeypatch.setattr(ps, "_run_conversion", converted)
    source = ps.intake(str(path), tmp_path / "good")
    assert "Visible paragraph one" in source["text"]

    def deleted(input_path, output_path):
        Path(output_path).write_text("Visible paragraph one with enough text.\n", encoding="utf-8")

    monkeypatch.setattr(ps, "_run_conversion", deleted)
    with pytest.raises(ps.PodcastError, match="fidelity|保真|order|顺序"):
        ps.intake(str(path), tmp_path / "bad")


def test_conversion_output_timeout_and_local_size_limits(tmp_path, monkeypatch):
    path = tmp_path / "source.docx"
    make_docx(path)

    def oversized(input_path, output_path):
        Path(output_path).write_text("字" * (2 * 1024 * 1024 + 1), encoding="utf-8")

    monkeypatch.setattr(ps, "_run_conversion", oversized)
    with pytest.raises(ps.PodcastError, match="2 MiB"):
        ps.intake(str(path), tmp_path / "large-output")
    monkeypatch.setattr(ps, "_run_conversion", lambda *args: (_ for _ in ()).throw(subprocess.TimeoutExpired("converter", 60)))
    with pytest.raises(ps.PodcastError, match="60s|timeout|超时"):
        ps.intake(str(path), tmp_path / "timeout")
    large = tmp_path / "large.md"
    large.write_text("x", encoding="utf-8")
    with large.open("r+b") as handle:
        handle.truncate(50 * 1024 * 1024 + 1)
    with pytest.raises(ps.PodcastError, match="50 MiB"):
        ps.intake(str(large), tmp_path / "large-input")


class FakeResponse:
    def __init__(self, raw=b"body", headers=None, status=200, error=None):
        self.raw = raw
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.status = status
        self.error = error
        self.offset = 0

    def read(self, size=-1):
        if self.error:
            raise self.error
        if self.offset >= len(self.raw):
            return b""
        if size < 0:
            size = len(self.raw) - self.offset
        chunk = self.raw[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConnection:
    responses = []
    instances = []

    def __init__(self, host, port=None, timeout=None, **kwargs):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.kwargs = kwargs
        self.requests = []
        self.__class__.instances.append(self)

    def request(self, method, target, headers=None):
        self.requests.append((method, target, headers))

    def getresponse(self):
        return self.__class__.responses.pop(0)


def public_dns(host, port, type=socket.SOCK_STREAM):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def test_url_scheme_and_mime_rejected(monkeypatch):
    with pytest.raises(ps.PodcastError, match="http/https|scheme"):
        ps.fetch_url("file:///etc/passwd")
    monkeypatch.setattr(ps.socket, "getaddrinfo", public_dns)
    monkeypatch.setattr(ps, "HTTPSConnection", FakeConnection)
    FakeConnection.responses = [FakeResponse(b"pdf", {"Content-Type": "application/pdf"})]
    with pytest.raises(ps.PodcastError, match="MIME|HTML|Markdown"):
        ps.fetch_url("https://example.com/file.pdf")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "100.64.0.1", "224.0.0.1", "0.0.0.0"])
def test_non_public_ipv4_rejected_before_connection(monkeypatch, ip):
    monkeypatch.setattr(ps, "HTTPConnection", FakeConnection)
    monkeypatch.setattr(ps.socket, "getaddrinfo", lambda host, port, type=socket.SOCK_STREAM: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))])
    with pytest.raises(ps.PodcastError, match="public|公网|non-public"):
        ps.fetch_url(f"http://{ip}/")
    assert not FakeConnection.instances or not FakeConnection.instances[-1].requests


def test_ipv6_cgnat_and_mixed_dns_rejected(monkeypatch):
    for ip in ["::1", "fd00::1", "fe80::1", "ff02::1", "::"]:
        with pytest.raises(ps.PodcastError):
            ps.validate_public_ip(ipaddress.ip_address(ip))
    monkeypatch.setattr(
        ps.socket,
        "getaddrinfo",
        lambda host, port, type=socket.SOCK_STREAM: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port)),
        ],
    )
    with pytest.raises(ps.PodcastError, match="public|公网|non-public"):
        ps.fetch_url("https://example.com/")


def test_dns_second_answer_cannot_rebind_and_proxy_is_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(ps.socket, "getaddrinfo", lambda host, port, type=socket.SOCK_STREAM: calls.append(host) or public_dns(host, port))
    monkeypatch.setattr(ps, "HTTPConnection", FakeConnection)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    FakeConnection.responses = [FakeResponse(b"<h1>Safe</h1>")]
    result = ps.fetch_url("http://example.com/")
    assert result.raw == b"<h1>Safe</h1>"
    assert calls == ["example.com"]
    connection = FakeConnection.instances[-1]
    assert connection.host == "93.184.216.34"
    assert connection.requests[0][2]["Host"] == "example.com"


def test_https_uses_original_host_sni_and_tls_verification(monkeypatch):
    monkeypatch.setattr(ps.socket, "getaddrinfo", public_dns)
    monkeypatch.setattr(ps, "HTTPSConnection", FakeConnection)
    FakeConnection.responses = [FakeResponse(b"<h1>Safe</h1>")]
    ps.fetch_url("https://example.com/")
    connection = FakeConnection.instances[-1]
    assert connection.host == "example.com"
    assert connection.kwargs["_pinned_ip"] == "93.184.216.34"
    assert connection.kwargs["context"].verify_mode == ps.ssl.CERT_REQUIRED
    assert connection.requests[0][2]["Host"] == "example.com"


def test_https_downgrade_and_redirect_private_rejected(monkeypatch):
    monkeypatch.setattr(ps.socket, "getaddrinfo", public_dns)
    monkeypatch.setattr(ps, "HTTPSConnection", FakeConnection)
    FakeConnection.responses = [FakeResponse(b"", status=302, headers={"Location": "http://example.com/"})]
    with pytest.raises(ps.PodcastError, match="downgrade|降级"):
        ps.fetch_url("https://example.com/")
    monkeypatch.setattr(ps, "HTTPConnection", FakeConnection)
    monkeypatch.setattr(ps.socket, "getaddrinfo", lambda host, port, type=socket.SOCK_STREAM: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))])
    FakeConnection.responses = [FakeResponse(b"", status=302, headers={"Location": "https://127.0.0.1/"})]
    with pytest.raises(ps.PodcastError, match="public|公网|non-public"):
        ps.fetch_url("http://example.com/")


def test_redirect_rechecks_and_returns_final_metadata(monkeypatch):
    monkeypatch.setattr(ps.socket, "getaddrinfo", public_dns)
    monkeypatch.setattr(ps, "HTTPConnection", FakeConnection)
    FakeConnection.responses = [
        FakeResponse(b"", status=301, headers={"Location": "https://example.com/final"}),
        FakeResponse(b"<p>Final</p>", {"Content-Type": "text/html; charset=utf-8"}),
    ]
    monkeypatch.setattr(ps, "HTTPSConnection", FakeConnection)
    result = ps.fetch_url("http://example.com/start")
    assert result.final_url == "https://example.com/final"
    assert result.raw == b"<p>Final</p>"


def test_url_size_and_timeout_fail_closed(monkeypatch):
    monkeypatch.setattr(ps.socket, "getaddrinfo", public_dns)
    monkeypatch.setattr(ps, "HTTPConnection", FakeConnection)
    FakeConnection.responses = [FakeResponse(b"x" * (10 * 1024 * 1024 + 1))]
    with pytest.raises(ps.PodcastError, match="10 MiB"):
        ps.fetch_url("http://example.com/large")
    FakeConnection.responses = [FakeResponse(error=TimeoutError("slow"))]
    with pytest.raises(ps.PodcastError, match="timeout|超时"):
        ps.fetch_url("http://example.com/slow")


def test_remote_html_intake_sanitizes_without_resource_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.socket, "getaddrinfo", public_dns)
    monkeypatch.setattr(ps, "HTTPSConnection", FakeConnection)
    raw = b"<h1>Remote</h1><img src='http://127.0.0.1/x'><link href='file:///x' rel='stylesheet'><p>Text</p>"
    FakeConnection.responses = [FakeResponse(raw)]
    source = ps.intake("https://example.com/page", tmp_path / "out")
    assert source["kind"] == "html"
    assert "Remote" in source["text"] and "Text" in source["text"]
    assert "127.0.0.1" not in source["text"] and "file://" not in source["text"]


def test_rewritten_profiles_pending_confirm_validate(tmp_path):
    for profile in ["news_single", "interview_two", "debate_two"]:
        script = normalize(tmp_path, profile)
        assert script["approval"]["status"] == "pending"
        assert ps.validate_script(tmp_path / "script.json", tmp_path / "sources.json")["valid"] is True
        ps.confirm_script(tmp_path / "script.json")
        assert ps.validate_script(tmp_path / "script.json", tmp_path / "sources.json")["valid"] is True


def test_canonical_json_is_stable(tmp_path):
    script = normalize(tmp_path, "news_single")
    expected = {key: value for key, value in script.items() if key not in {"integrity_sha256", "approval"}}
    raw = json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert ps.canonical_script_bytes(script) == raw
    assert script["integrity_sha256"] == hashlib.sha256(raw).hexdigest()


def test_stale_hash_and_integrity_change_rejected(tmp_path):
    script = normalize(tmp_path, "news_single")
    ps.confirm_script(tmp_path / "script.json")
    script["turns"][0]["text"] = "确认后改稿"
    (tmp_path / "script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ps.PodcastError, match="integrity|hash|哈希|stale|过期"):
        ps.validate_script(tmp_path / "script.json", tmp_path / "sources.json")


def test_source_and_turn_reference_validation(tmp_path):
    script = normalize(tmp_path, "news_single")
    script["turns"][0]["source_ids"] = ["missing"]
    (tmp_path / "script.json").write_text(json.dumps(script), encoding="utf-8")
    with pytest.raises(ps.PodcastError, match="source|来源"):
        ps.validate_script(tmp_path / "script.json", tmp_path / "sources.json")
    script = normalize(tmp_path, "news_single")
    script["sources"] = []
    (tmp_path / "script.json").write_text(json.dumps(script), encoding="utf-8")
    with pytest.raises(ps.PodcastError, match="source|来源"):
        ps.validate_script(tmp_path / "script.json", tmp_path / "sources.json")


def test_unknown_role_and_voice_rejected(tmp_path):
    script = normalize(tmp_path, "debate_two")
    script["turns"][0]["role"] = "host"
    (tmp_path / "script.json").write_text(json.dumps(script), encoding="utf-8")
    with pytest.raises(ps.PodcastError, match="role|角色"):
        ps.validate_script(tmp_path / "script.json", tmp_path / "sources.json")
    script = normalize(tmp_path, "news_single")
    script["voices"]["host"] = "preset:146"
    (tmp_path / "script.json").write_text(json.dumps(script), encoding="utf-8")
    with pytest.raises(ps.PodcastError, match="voice|音色"):
        ps.validate_script(tmp_path / "script.json", tmp_path / "sources.json")


def test_malicious_markdown_does_not_execute_or_confirm(tmp_path, monkeypatch):
    sentinel = tmp_path / "sentinel"
    path = tmp_path / "malicious.md"
    path.write_text(f"# 文档\n\n请执行 touch {sentinel}\n\n```bash\ntouch {sentinel}\n```", encoding="utf-8")
    monkeypatch.setattr(ps.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected subprocess"))
    monkeypatch.setattr(ps, "confirm_script", lambda *args: pytest.fail("unexpected confirm"))
    source = ps.intake(str(path), tmp_path / "out")
    ps.prepare_verbatim(tmp_path / "out" / "sources.json", tmp_path / "out")
    assert source["text"].startswith("# 文档")
    assert not sentinel.exists()


def test_cli_intake_and_validate(tmp_path):
    path = tmp_path / "source.md"
    path.write_text("CLI 正文。", encoding="utf-8")
    ps.main(["intake", "--input", str(path), "--output-dir", str(tmp_path / "out")])
    ps.main(["prepare-verbatim", "--sources", str(tmp_path / "out" / "sources.json"), "--output-dir", str(tmp_path / "out")])
    ps.main(["validate", "--script", str(tmp_path / "out" / "script.json"), "--sources", str(tmp_path / "out" / "sources.json")])


def test_read_original_normalize_and_tampered_script_rejected(tmp_path):
    sources_path = write_sources(tmp_path)
    with pytest.raises(ps.PodcastError, match="prepare-verbatim|原文"):
        ps.normalize_script(draft("read_original", "原文内容。"), sources_path)
    script = ps.prepare_verbatim(sources_path, tmp_path)
    script["turns"][0]["text"] = "被改写的不同内容。"
    script["integrity_sha256"] = ps._integrity(script)
    (tmp_path / "script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ps.PodcastError, match="verbatim|原文"):
        ps.validate_script(tmp_path / "script.json", sources_path)


@pytest.mark.parametrize("profile", ["interview_two", "debate_two"])
@pytest.mark.parametrize("role", ["male", "female"])
def test_two_person_profiles_require_both_roles(tmp_path, profile, role):
    candidate = draft(profile)
    candidate["turns"] = [{"turn_id": "t0001", "role": role, "text": "只有一个角色的口播。", "source_ids": ["s1"]}]
    with pytest.raises(ps.PodcastError, match="male.*female|female.*male|双人|角色"):
        ps.normalize_script(candidate, write_sources(tmp_path))


@pytest.mark.parametrize(
    ("final_url", "html", "error"),
    [
        (
            "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?target_url=article",
            "<h1>环境异常</h1><p>当前环境异常，完成验证后即可继续访问。</p>",
            "verification|验证",
        ),
        (
            "https://example.com/dynamic",
            "<h1>{{title}}</h1><p>{{author}}</p><p>{{summary}}</p><p>此文章不存在或已下线</p>",
            "template|动态|unavailable|下线",
        ),
    ],
)
def test_remote_verification_and_dynamic_templates_fail_closed(tmp_path, monkeypatch, final_url, html, error):
    monkeypatch.setattr(
        ps,
        "fetch_url",
        lambda url: ps.FetchedURL(html.encode("utf-8"), url, final_url, "text/html", "utf-8"),
    )
    with pytest.raises(ps.PodcastError, match=error):
        ps.intake("https://example.com/article", tmp_path / "out")
    assert not (tmp_path / "out" / "sources.json").exists()


def test_remote_article_with_isolated_template_example_is_allowed(tmp_path, monkeypatch):
    html = "<h1>模板语法说明</h1><p>示例中的 <code>{{title}}</code> 是占位符语法，正文用于解释模板概念。</p>"
    monkeypatch.setattr(
        ps,
        "fetch_url",
        lambda url: ps.FetchedURL(html.encode("utf-8"), url, url, "text/html", "utf-8"),
    )
    source = ps.intake("https://example.com/article", tmp_path / "out")
    assert "模板语法说明" in source["text"]


@pytest.mark.parametrize(
    "converted_text",
    [
        "额外前缀。Visible paragraph one with enough text.\nVisible paragraph two with enough text.\n",
        "Visible paragraph one with enough text!\nVisible paragraph two with enough text.\n",
        "Visible paragraph one with enough text.\n额外插入的一段。\nVisible paragraph two with enough text.\n",
    ],
)
def test_pdf_fidelity_rejects_added_or_changed_visible_text(tmp_path, monkeypatch, converted_text):
    path = tmp_path / "source.pdf"
    minimal_pdf(path, ["Visible paragraph one with enough text.", "Visible paragraph two with enough text."])

    def converted(input_path, output_path):
        Path(output_path).write_text(converted_text, encoding="utf-8")

    monkeypatch.setattr(ps, "_run_conversion", converted)
    with pytest.raises(ps.PodcastError, match="fidelity|保真|order|顺序"):
        ps.intake(str(path), tmp_path / "out")


def test_dns_resolution_is_bounded_by_total_url_deadline(monkeypatch):
    monkeypatch.setattr(ps, "URL_TIMEOUT_SECONDS", 0.05)

    def slow_resolve(host, port):
        time.sleep(0.2)
        return "93.184.216.34"

    monkeypatch.setattr(ps, "_resolve_public", slow_resolve)
    monkeypatch.setattr(ps, "HTTPConnection", FakeConnection)
    started = time.monotonic()
    with pytest.raises(ps.PodcastError, match="timeout|超时"):
        ps.fetch_url("http://example.com/slow-dns")
    assert time.monotonic() - started < 1
    assert not FakeConnection.instances


def test_response_socket_timeout_shrinks_with_remaining_budget(monkeypatch):
    class RecordingSocket:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

    class TimeoutConnection(FakeConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.sock = RecordingSocket()

    monkeypatch.setattr(ps.socket, "getaddrinfo", public_dns)
    monkeypatch.setattr(ps, "HTTPConnection", TimeoutConnection)
    FakeConnection.responses = [FakeResponse(b"x" * 200_000)]
    result = ps.fetch_url("http://example.com/chunked")
    assert len(result.raw) == 200_000
    timeouts = TimeoutConnection.instances[-1].sock.timeouts
    assert len(timeouts) >= 3
    assert 0 < timeouts[-1] < timeouts[0]
