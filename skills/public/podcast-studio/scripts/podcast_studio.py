#!/usr/bin/env python3
"""Deterministic podcast source intake, script integrity, and approval contracts."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path

HTTPConnection = http.client.HTTPConnection
HTTPSConnection = http.client.HTTPSConnection

SCHEMA_VERSION = "podcast-studio.v1"
PROFILES = {"read_original", "news_single", "interview_two", "debate_two"}
MALE_STOCK = "doubao:zh_male_m191_uranus_bigtts"
FEMALE_STOCK = "doubao:zh_female_vv_uranus_bigtts"
MAX_LOCAL_BYTES = 50 * 1024 * 1024
MAX_URL_BYTES = 10 * 1024 * 1024
MAX_CONVERTED_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
MAX_SEGMENT_CHARS = 300
CONVERT_TIMEOUT_SECONDS = 60
URL_TIMEOUT_SECONDS = 30
MAX_REDIRECTS = 5
PPT_SCRIPTS = Path(__file__).resolve().parents[2] / "ppt-master" / "scripts"
SOURCE_TO_MD = PPT_SCRIPTS / "source_to_md.py"


class PodcastError(ValueError):
    pass


@dataclass
class FetchedURL:
    raw: bytes
    requested_url: str
    final_url: str
    mime: str
    charset: str


def _read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, value) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_text(text: str) -> str:
    return _sha_bytes(text.encode("utf-8"))


def canonical_script_bytes(script: dict) -> bytes:
    canonical = {key: value for key, value in script.items() if key not in {"integrity_sha256", "approval"}}
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _integrity(script: dict) -> str:
    return _sha_bytes(canonical_script_bytes(script))


def _source_summary(source: dict) -> dict:
    return {
        "source_id": source["source_id"],
        "raw_sha256": source["raw_sha256"],
        "extracted_text_sha256": source["extracted_text_sha256"],
    }


def _decode_bytes(raw: bytes, content_type: str = "") -> tuple[str, str]:
    match = re.search(r"charset\s*=\s*['\"]?([A-Za-z0-9._-]+)", content_type, re.I)
    candidates = [match.group(1)] if match else []
    candidates.extend(["utf-8-sig", "gb18030"])
    for encoding in candidates:
        try:
            return raw.decode(encoding), encoding.lower()
        except (UnicodeDecodeError, LookupError):
            continue
    raise PodcastError("Unable to decode text as UTF-8 or GB18030")


def _check_text(text: str) -> None:
    if not text.strip():
        raise PodcastError("Extracted text is empty / 提取文本为空")
    if len(text) > MAX_TEXT_CHARS:
        raise PodcastError(f"Extracted text has {len(text)} characters; the limit is {MAX_TEXT_CHARS:,}; split the input")


def _sanitize_html(html: str) -> tuple[str, list[str]]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise PodcastError("BeautifulSoup is required in the sandbox image") from exc

    soup = BeautifulSoup(html, "html.parser")
    removed = 0
    for tag in list(soup.find_all(["script", "style", "link", "iframe", "object", "embed", "img", "audio", "video", "source", "track", "meta"])):
        tag.decompose()
        removed += 1
    resource_attributes = {"src", "srcset", "href", "poster", "data", "action", "formaction", "background", "cite", "longdesc", "usemap", "style", "srcdoc"}
    for tag in soup.find_all(True):
        for attribute in list(tag.attrs):
            if attribute.lower() in resource_attributes or attribute.lower().startswith("on"):
                del tag.attrs[attribute]
                removed += 1
    warnings = [f"Removed {removed} non-text HTML resource element(s)/attribute(s); images are not spoken"] if removed else []
    return str(soup), warnings


def _load_web_to_md():
    path = PPT_SCRIPTS / "source_to_md" / "web_to_md.py"
    spec = importlib.util.spec_from_file_location("podcast_ppt_web_to_md", path)
    if spec is None or spec.loader is None:
        raise PodcastError(f"Cannot load HTML converter: {path}")
    module = importlib.util.module_from_spec(spec)
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(module)
    return module


def convert_html_bytes(raw: bytes, requested_url: str, final_url: str, headers: dict) -> str:
    content_type = headers.get("Content-Type", "") if headers else ""
    html, _ = _decode_bytes(raw, content_type)
    sanitized, _ = _sanitize_html(html)
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(sanitized, "html.parser")
    converter = _load_web_to_md()
    return converter.simple_html_to_markdown_traversal(soup, page_url=final_url or requested_url).strip()


def check_docx_zip_limits(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > 2048:
        raise PodcastError("DOCX has more than 2048 ZIP entries")
    total = 0
    for info in infos:
        if info.file_size > 25 * 1024 * 1024:
            raise PodcastError("DOCX ZIP entry exceeds 25 MiB")
        total += info.file_size
        if total > 100 * 1024 * 1024:
            raise PodcastError("DOCX total uncompressed size exceeds 100 MiB")


def _run_conversion(input_path: str, output_path: str) -> None:
    if not SOURCE_TO_MD.is_file():
        raise PodcastError(f"PPT Master converter not found: {SOURCE_TO_MD}")
    command = [sys.executable, str(SOURCE_TO_MD), input_path, "-o", output_path]
    if Path(input_path).suffix.lower() == ".pdf":
        command.append("--no-images")
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=CONVERT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise PodcastError("Document conversion exceeded the 60s deadline / 转换超时") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"")[:1000]
        raise PodcastError(f"Document conversion failed: {detail.decode('utf-8', errors='replace')}")


def _clean_converted_markdown(text: str, source_path: str) -> str:
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        expected = Path(source_path).stem.replace("-", " ").strip().lower()
        actual = lines[0][2:].strip().lower()
        if actual == expected or actual.replace(" ", "-") == Path(source_path).stem.lower():
            lines = lines[1:]
    return "\n".join(lines).strip()


def _convert_document(input_path: str, output_dir: Path) -> tuple[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{Path(input_path).stem}.converted.md"
    try:
        _run_conversion(input_path, str(output_path))
    except subprocess.TimeoutExpired as exc:
        raise PodcastError("Document conversion exceeded the 60s deadline / 转换超时") from exc
    if not output_path.is_file():
        raise PodcastError("Converter returned success without an output file")
    size = output_path.stat().st_size
    if size > MAX_CONVERTED_BYTES:
        raise PodcastError("Converted output exceeds 2 MiB")
    text = output_path.read_text(encoding="utf-8")
    if len(text.encode("utf-8")) > MAX_CONVERTED_BYTES:
        raise PodcastError("Converted output exceeds 2 MiB")
    return _clean_converted_markdown(text, input_path), "ppt-master/source_to_md.py --no-images"


def _pdf_pages_text(input_path: str) -> list[str]:
    try:
        import fitz

        with fitz.open(input_path) as document:
            return [page.get_text() for page in document]
    except ImportError:
        result = subprocess.run(["pdftotext", "-layout", input_path, "-"], check=False, capture_output=True, timeout=CONVERT_TIMEOUT_SECONDS)
        if result.returncode != 0:
            raise PodcastError("Unable to read PDF text for fidelity checks")
        return result.stdout.decode("utf-8", errors="replace").split("\f")


def _normalized_visible(text: str) -> str:
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r"!\[[^]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text, flags=re.UNICODE)


def _check_pdf_fidelity(input_path: str, converted: str) -> None:
    pages = _pdf_pages_text(input_path)
    if not pages or sum(len(re.sub(r"\s", "", page)) for page in pages) < 40:
        raise PodcastError("Sparse/scanned PDF text is unsupported / PDF 文本稀疏")
    normalized = _normalized_visible(converted)
    cursor = 0
    for page in pages:
        expected = _normalized_visible(page)
        if not expected:
            continue
        position = normalized.find(expected, cursor)
        if position < 0:
            raise PodcastError("PDF conversion fidelity/order check failed / PDF 转换保真或顺序校验失败")
        cursor = position + len(expected)


def validate_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    candidate = getattr(address, "ipv4_mapped", None) or address
    if not candidate.is_global or candidate.is_unspecified or candidate.is_loopback or candidate.is_private or candidate.is_link_local or candidate.is_multicast or candidate.is_reserved:
        raise PodcastError(f"URL resolves to a non-public address / 非公网地址: {candidate}")


def _resolve_public(host: str, port: int) -> str:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise PodcastError(f"DNS resolution failed: {host}") from exc
    if not records:
        raise PodcastError("DNS returned no addresses")
    addresses = []
    for record in records:
        raw = record[4][0]
        address = ipaddress.ip_address(raw)
        validate_public_ip(address)
        addresses.append(raw)
    return addresses[0]


class PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, _pinned_ip: str, **kwargs):
        self._pinned_ip = _pinned_ip
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        family = socket.AF_INET6 if ":" in self._pinned_ip else socket.AF_INET
        raw = socket.socket(family, socket.SOCK_STREAM)
        raw.settimeout(self.timeout)
        raw.connect((self._pinned_ip, self.port))
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


HTTPSConnection = PinnedHTTPSConnection


def _connection_for(parts: urllib.parse.SplitResult, host: str, port: int, pinned_ip: str, timeout: float):
    if parts.scheme == "https":
        return HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context(), _pinned_ip=pinned_ip)
    return HTTPConnection(pinned_ip, port, timeout=timeout)


def _parse_http_url(url: str) -> tuple[urllib.parse.SplitResult, str, int]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise PodcastError("Only http/https URL scheme is allowed")
    if not parts.hostname or parts.username or parts.password:
        raise PodcastError("URL must have a public hostname and no credentials")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        host = parts.hostname.encode("idna").decode("ascii")
    except (ValueError, UnicodeError) as exc:
        raise PodcastError("Invalid URL host/port") from exc
    return parts, host, port


def fetch_url(url: str) -> FetchedURL:
    requested = url
    current = url
    started = time.monotonic()
    for redirect_count in range(MAX_REDIRECTS + 1):
        parts, host, port = _parse_http_url(current)
        pinned_ip = _resolve_public(host, port)
        timeout = max(0.1, URL_TIMEOUT_SECONDS - (time.monotonic() - started))
        if timeout <= 0:
            raise PodcastError("URL fetch exceeded total timeout / URL 获取超时")
        connection = _connection_for(parts, host, port, pinned_ip, timeout)
        target = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        host_header = host if port in {80, 443} else f"{host}:{port}"
        try:
            connection.request("GET", target, headers={"Host": host_header, "User-Agent": "podcast-studio/1.0", "Accept": "text/html,text/markdown,text/plain", "Connection": "close"})
            response = connection.getresponse()
            status = response.status
            if status in {301, 302, 303, 307, 308}:
                if redirect_count >= MAX_REDIRECTS:
                    raise PodcastError("Too many URL redirects")
                location = response.getheader("Location")
                if not location:
                    raise PodcastError("Redirect has no Location header")
                next_url = urllib.parse.urljoin(current, location)
                next_parts, _, _ = _parse_http_url(next_url)
                if parts.scheme == "https" and next_parts.scheme != "https":
                    raise PodcastError("HTTPS to HTTP downgrade is forbidden / 禁止 HTTPS 降级")
                current = next_url
                continue
            if status != 200:
                raise PodcastError(f"URL returned HTTP {status}")
            mime = (response.getheader("Content-Type", "") or "").split(";", 1)[0].strip().lower()
            if mime not in {"text/html", "application/xhtml+xml", "text/markdown", "text/plain"}:
                raise PodcastError("Only HTML/Markdown MIME is supported")
            content_type = response.getheader("Content-Type", "") or ""
            _, charset = _decode_bytes(b"", content_type) if False else ("", "utf-8")
            match = re.search(r"charset\s*=\s*['\"]?([A-Za-z0-9._-]+)", content_type, re.I)
            charset = match.group(1).lower() if match else "utf-8"
            chunks = []
            total = 0
            while True:
                if time.monotonic() - started > URL_TIMEOUT_SECONDS:
                    raise PodcastError("URL fetch exceeded total timeout / URL 获取超时")
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_URL_BYTES:
                    raise PodcastError("URL response exceeds 10 MiB")
                chunks.append(chunk)
            return FetchedURL(b"".join(chunks), requested, current, mime, charset)
        except TimeoutError as exc:
            raise PodcastError("URL fetch timed out / URL 获取超时") from exc
        except PodcastError:
            raise
        except OSError as exc:
            raise PodcastError(f"URL fetch failed: {type(exc).__name__}") from exc
        finally:
            close = getattr(connection, "close", None)
            if close:
                close()
    raise PodcastError("URL redirect limit reached")


def _source_kind(input_value: str) -> str:
    if input_value.startswith(("http://", "https://")):
        return "url"
    path = Path(input_value)
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return "markdown"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    raise PodcastError("Only local PDF/DOCX/MD/HTML or safe HTML/Markdown URLs are supported")


def intake(input_value: str, output_dir: str | Path, source_id: str = "s1") -> dict:
    output = Path(output_dir)
    kind = _source_kind(input_value)
    requested_url = input_value if kind == "url" else None
    final_url = requested_url
    warnings: list[str] = []
    if kind == "url":
        fetched = fetch_url(input_value)
        raw = fetched.raw
        final_url = fetched.final_url
        mime = fetched.mime
        charset = fetched.charset
        kind = "html" if mime in {"text/html", "application/xhtml+xml"} else "markdown"
        if kind == "html":
            text = convert_html_bytes(raw, requested_url or input_value, final_url or input_value, {"Content-Type": f"{mime}; charset={charset}"})
            converter = "ppt-master/simple_html_to_markdown_traversal (sanitized)"
            warnings.append("Remote HTML resources removed before conversion; images are not spoken")
        else:
            text, charset = _decode_bytes(raw, f"charset={charset}")
            converter = "safe-url passthrough"
    else:
        path = Path(input_value)
        if not path.is_file():
            raise PodcastError(f"Input file not found: {path}")
        size = path.stat().st_size
        if size > MAX_LOCAL_BYTES:
            raise PodcastError("Local input exceeds 50 MiB")
        raw = path.read_bytes()
        mime = {
            ".md": "text/markdown",
            ".markdown": "text/markdown",
            ".txt": "text/plain",
            ".html": "text/html",
            ".htm": "text/html",
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }[path.suffix.lower()]
        charset = "utf-8"
        if kind in {"markdown", "html"}:
            text, charset = _decode_bytes(raw, mime)
            if kind == "html":
                sanitized, warnings = _sanitize_html(text)
                text = convert_html_bytes(sanitized.encode("utf-8"), "", "", {"Content-Type": f"{mime}; charset={charset}"})
                converter = "ppt-master/simple_html_to_markdown_traversal (sanitized)"
            else:
                converter = "passthrough"
        elif kind == "docx":
            with zipfile.ZipFile(path) as archive:
                check_docx_zip_limits(archive.infolist())
            text, converter = _convert_document(str(path), output)
        else:
            pages = _pdf_pages_text(str(path))
            if not pages or sum(len(re.sub(r"\s", "", page)) for page in pages) < 40:
                raise PodcastError("Sparse/scanned PDF text is unsupported / PDF 文本稀疏")
            text, converter = _convert_document(str(path), output)
            _check_pdf_fidelity(str(path), text)
            warnings.append("PDF images are not spoken")
    _check_text(text)
    source = {
        "source_id": source_id,
        "kind": kind,
        "requested_url": requested_url,
        "final_url": final_url,
        "mime": mime,
        "charset": charset,
        "converter": converter,
        "warnings": warnings,
        "text": text,
        "raw_sha256": _sha_bytes(raw),
        "extracted_text_sha256": _sha_text(text),
    }
    sources_path = output / "sources.json"
    sources = [source]
    if sources_path.is_file():
        existing = _read_json(sources_path).get("sources", [])
        sources = [item for item in existing if item.get("source_id") != source_id] + [source]
    _write_json(sources_path, {"schema_version": SCHEMA_VERSION, "sources": sources})
    return source


def _load_sources(path: str | Path) -> list[dict]:
    payload = _read_json(path)
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(payload.get("sources"), list):
        raise PodcastError("Invalid sources.json schema")
    sources = payload["sources"]
    ids = [source.get("source_id") for source in sources]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        raise PodcastError("Duplicate or missing source_id")
    for source in sources:
        text = source.get("text")
        if not isinstance(text, str):
            raise PodcastError("Source is missing extracted text")
        if source.get("extracted_text_sha256") != _sha_text(text):
            raise PodcastError("Source extracted text hash drift")
    return sources


def _chunk_text(text: str, source_id: str) -> list[dict]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + 4000, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n\n", start, end), text.rfind("。", start, end), text.rfind("\n", start, end))
            if boundary > start:
                end = boundary + 1
        piece = text[start:end]
        chunks.append({"source_id": source_id, "start": start, "end": end, "text": piece, "text_sha256": _sha_text(piece)})
        start = end
    return chunks


def _approval_for(profile: str) -> dict:
    if profile == "read_original":
        return {"required": False, "status": "not_required", "approved_sha256": None}
    return {"required": True, "status": "pending", "approved_sha256": None}


def _finalize_script(draft: dict, sources: list[dict]) -> dict:
    profile = draft.get("profile")
    if profile not in PROFILES:
        raise PodcastError("Unknown podcast profile")
    script = {key: value for key, value in draft.items() if key not in {"integrity_sha256", "approval"}}
    script["schema_version"] = SCHEMA_VERSION
    script["sources"] = [_source_summary(source) for source in sources]
    script["approval"] = _approval_for(profile)
    script["integrity_sha256"] = _integrity(script)
    return script


def prepare_verbatim(sources_path: str | Path, output_dir: str | Path) -> dict:
    sources = _load_sources(sources_path)
    if not sources:
        raise PodcastError("No sources supplied")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    chunks = []
    turns = []
    for source in sources:
        _check_text(source["text"])
        source_chunks = _chunk_text(source["text"], source["source_id"])
        chunks.extend(source_chunks)
        for chunk in source_chunks:
            turns.append({"turn_id": f"t{len(turns) + 1:04d}", "role": "host", "text": chunk["text"], "source_ids": [source["source_id"]]})
    with (output / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    draft = {
        "schema_version": SCHEMA_VERSION,
        "profile": "read_original",
        "title": "文档原文朗读",
        "sources": [_source_summary(source) for source in sources],
        "voices": {"host": MALE_STOCK},
        "turns": turns,
    }
    script = _finalize_script(draft, sources)
    _write_json(output / "script.json", script)
    if Path(sources_path).resolve() != (output / "sources.json").resolve():
        _write_json(output / "sources.json", {"schema_version": SCHEMA_VERSION, "sources": sources})
    _validate_script(script, sources)
    return script


def normalize_script(draft: dict, sources_path: str | Path) -> dict:
    sources = _load_sources(sources_path)
    script = _finalize_script(draft, sources)
    _validate_script(script, sources, require_current_approval=False)
    output = Path(sources_path).parent / "script.json"
    _write_json(output, script)
    return script


def confirm_script(script_path: str | Path) -> dict:
    path = Path(script_path)
    script = _read_json(path)
    if script.get("profile") == "read_original":
        raise PodcastError("read_original does not require approval")
    approval = script.get("approval", {})
    if approval.get("required") is not True or approval.get("status") != "pending":
        raise PodcastError("Script is not pending approval")
    integrity = _integrity(script)
    if script.get("integrity_sha256") != integrity:
        raise PodcastError("Script integrity hash drift before confirmation")
    script["approval"] = {"required": True, "status": "approved", "approved_sha256": integrity}
    _write_json(path, script)
    return script


def _validate_script(script: dict, sources: list[dict], require_current_approval: bool = True) -> None:
    if script.get("schema_version") != SCHEMA_VERSION or script.get("profile") not in PROFILES:
        raise PodcastError("Invalid script schema/profile")
    if not isinstance(script.get("title"), str) or not script["title"].strip():
        raise PodcastError("Script title is required")
    profile = script["profile"]
    source_map = {source["source_id"]: source for source in sources}
    summaries = script.get("sources")
    if not isinstance(summaries, list) or not summaries:
        raise PodcastError("Script sources are required / 缺少来源")
    seen = set()
    for summary in summaries:
        source_id = summary.get("source_id")
        if source_id in seen or source_id not in source_map:
            raise PodcastError("Missing or duplicate script source reference / 来源引用错误")
        seen.add(source_id)
        full = source_map[source_id]
        if summary.get("raw_sha256") != full.get("raw_sha256") or summary.get("extracted_text_sha256") != full.get("extracted_text_sha256"):
            raise PodcastError("Script source hash drift / 来源哈希漂移")
    expected_voices = {"host": MALE_STOCK} if profile in {"read_original", "news_single"} else {"male": MALE_STOCK, "female": FEMALE_STOCK}
    if script.get("voices") != expected_voices:
        raise PodcastError("Unknown or profile-incompatible voice / 音色错误")
    turns = script.get("turns")
    if not isinstance(turns, list) or not turns:
        raise PodcastError("Script turns are required")
    allowed_roles = {"host"} if profile in {"read_original", "news_single"} else {"male", "female"}
    for index, turn in enumerate(turns, 1):
        if turn.get("turn_id") != f"t{index:04d}":
            raise PodcastError("turn_id must be sequential")
        if turn.get("role") not in allowed_roles:
            raise PodcastError("Unknown role for profile / 角色错误")
        if not isinstance(turn.get("text"), str) or not turn["text"].strip():
            raise PodcastError("Empty turn text")
        if len(turn["text"]) > MAX_TEXT_CHARS:
            raise PodcastError("Turn text exceeds 100,000 characters")
        refs = turn.get("source_ids")
        if not isinstance(refs, list) or not refs or any(ref not in seen for ref in refs):
            raise PodcastError("Turn references an unread source / turn 来源引用错误")
    if script.get("integrity_sha256") != _integrity(script):
        raise PodcastError("Script integrity hash mismatch / 完整性哈希错误或过期")
    approval = script.get("approval")
    if profile == "read_original":
        if approval != {"required": False, "status": "not_required", "approved_sha256": None}:
            raise PodcastError("read_original approval contract is invalid")
    else:
        if not isinstance(approval, dict) or approval.get("required") is not True:
            raise PodcastError("Approval is required for rewritten profiles")
        status = approval.get("status")
        if status == "pending":
            if approval.get("approved_sha256") is not None:
                raise PodcastError("Pending approval cannot carry a hash")
        elif status == "approved":
            if approval.get("approved_sha256") != script["integrity_sha256"]:
                raise PodcastError("Approval hash is stale / 确认哈希过期")
        else:
            raise PodcastError("Unknown approval status")


def validate_script(script_path: str | Path, sources_path: str | Path) -> dict:
    script = _read_json(script_path)
    sources = _load_sources(sources_path)
    _validate_script(script, sources)
    return {"valid": True, "profile": script["profile"], "integrity_sha256": script["integrity_sha256"]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    intake_parser = commands.add_parser("intake")
    intake_parser.add_argument("--input", required=True)
    intake_parser.add_argument("--output-dir", required=True)
    intake_parser.add_argument("--source-id", default="s1")
    prepare = commands.add_parser("prepare-verbatim")
    prepare.add_argument("--sources", required=True)
    prepare.add_argument("--output-dir", required=True)
    normalize = commands.add_parser("normalize-script")
    normalize.add_argument("--draft", required=True)
    normalize.add_argument("--sources", required=True)
    confirm = commands.add_parser("confirm")
    confirm.add_argument("--script", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--script", required=True)
    validate.add_argument("--sources", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "intake":
            source = intake(args.input, args.output_dir, args.source_id)
            print(json.dumps({"source_id": source["source_id"], "sources": str(Path(args.output_dir) / "sources.json")}, ensure_ascii=False))
        elif args.command == "prepare-verbatim":
            script = prepare_verbatim(args.sources, args.output_dir)
            print(json.dumps({"script": str(Path(args.output_dir) / "script.json"), "integrity_sha256": script["integrity_sha256"]}, ensure_ascii=False))
        elif args.command == "normalize-script":
            script = normalize_script(_read_json(args.draft), args.sources)
            print(json.dumps({"script": str(Path(args.sources).parent / "script.json"), "integrity_sha256": script["integrity_sha256"]}, ensure_ascii=False))
        elif args.command == "confirm":
            script = confirm_script(args.script)
            print(json.dumps({"approved": True, "integrity_sha256": script["integrity_sha256"]}, ensure_ascii=False))
        elif args.command == "validate":
            print(json.dumps(validate_script(args.script, args.sources), ensure_ascii=False))
        return 0
    except (PodcastError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
