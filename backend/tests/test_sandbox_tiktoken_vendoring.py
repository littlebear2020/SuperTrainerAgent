"""沙箱镜像 tiktoken 离线 vendoring 锚点（2026-11-20，ppt-master 执行包精确计数）。

背景：ppt-master `execution-context` 强制 `token_status=exact`（tiktoken o200k_base 精确计数），沙箱缺
tiktoken 时执行包全链路 fail-closed。不赌镜像源：开发机与容器内实测清华源包索引 403、公司网络对 pypi.org 静默丢包、
BPE 数据托管在 Azure blob——所以 wheel 与 BPE 缓存 vendoring 进 docker/sandbox/cache/（K16 离线来源），Dockerfile 构建期离线安装，
运行期 TIKTOKEN_CACHE_DIR 命中缓存、不联网。

这里钉两层（均不需要 Docker，进 CI）：
- 入库产物完整性：wheel 文件集 / sha256（PyPI 发布值）/ 平台标签与 ELF 架构、requirements 钉版本 + hash、
  BPE 缓存文件名（= sha1(下载 URL)）与 sha256，且真能经 tiktoken 在断网下加载并算出预期 token；
- Dockerfile 接线：COPY / ENV / RUN 锚点与顺序（离线、hash 校验、构建期冒烟）。
真实构建与镜像内执行包冒烟见 changelog 验收记录（需 Docker，不进 CI）。部署脚本的包索引另见
test_local_deploy_package_index.py。

同口味先例：test_sandbox_offline_readiness.py。升级版本/哈希须同步改 requirements.txt、wheel 文件与这里。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_DIR = REPO_ROOT / "docker" / "sandbox"
DOCKERFILE = SANDBOX_DIR / "Dockerfile"
WHEEL_DIR = SANDBOX_DIR / "cache" / "python-wheels"
BPE_DIR = SANDBOX_DIR / "cache" / "tiktoken"
REQUIREMENTS = WHEEL_DIR / "requirements.txt"

WHEEL_SOURCE = "docker/sandbox/cache/python-wheels/"
BPE_SOURCE = "docker/sandbox/cache/tiktoken/"

# PyPI 发布的 sha256（https://pypi.org/pypi/<pkg>/<ver>/json 的 digests.sha256）——独立于 requirements.txt 的
# 第二份真值：两处各抄一遍，才不会因一处笔误让「校验」自己通过。
EXPECTED_WHEELS = {
    "tiktoken-0.12.0-cp310-cp310-manylinux_2_28_x86_64.whl": "6de0da39f605992649b9cfa6f84071e3f9ef2cec458d08c5feb1b6f0ff62e134",
    "tiktoken-0.12.0-cp310-cp310-manylinux_2_28_aarch64.whl": "cde24cdb1b8a08368f709124f15b36ab5524aac5fa830cc3fdce9c03d4fb8030",
    "regex-2026.4.4-cp310-cp310-manylinux2014_x86_64.manylinux_2_17_x86_64.whl": "0540e5b733618a2f84e9cb3e812c8afa82e151ca8e19cf6c4e95c5a65198236f",
    "regex-2026.4.4-cp310-cp310-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl": "6780f008ee81381c737634e75c24e5a6569cc883c4f8e37a37917ee79efcafd9",
}
EXPECTED_PINS = {"tiktoken": "0.12.0", "regex": "2026.4.4"}
O200K_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
# tiktoken 自身 openai_public.o200k_base 内置的 expected_hash（加载缓存时同样按它校验）
O200K_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
ELF_MACHINE = {"x86_64": 0x3E, "aarch64": 0xB7}


def _arch(wheel_name: str) -> str:
    return "x86_64" if "x86_64" in wheel_name else "aarch64"


# ── 入库产物：wheel ─────────────────────────────────────────────────────────


def test_vendored_wheel_dir_has_exactly_the_expected_files():
    assert {p.name for p in WHEEL_DIR.iterdir()} == set(EXPECTED_WHEELS) | {"requirements.txt"}


@pytest.mark.parametrize("name", sorted(EXPECTED_WHEELS))
def test_vendored_wheel_matches_the_sha256_pypi_published(name: str):
    assert hashlib.sha256((WHEEL_DIR / name).read_bytes()).hexdigest() == EXPECTED_WHEELS[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_WHEELS))
def test_vendored_wheel_is_cp310_manylinux_with_native_code_for_its_own_arch(name: str):
    """基座是 Ubuntu 22.04 / Python 3.10 / glibc 2.35；文件名标 x86_64 的必须真是 x86_64 ELF（防两架构换错）。"""
    assert "-cp310-cp310-manylinux" in name
    with zipfile.ZipFile(WHEEL_DIR / name) as wheel:
        libs = [n for n in wheel.namelist() if n.endswith(".so")]
        assert libs, f"{name} 没有原生扩展"
        for lib in libs:
            header = wheel.read(lib)[:20]
            assert header[:4] == b"\x7fELF", f"{lib} 不是 ELF"
            machine = int.from_bytes(header[18:20], "little")
            assert machine == ELF_MACHINE[_arch(name)], f"{lib} 的 ELF 架构与文件名标注的 {_arch(name)} 不符"


def _requirement_pins() -> dict[str, tuple[str, set[str]]]:
    text = "\n".join(line.split("#", 1)[0] for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines())
    text = re.sub(r"\\\s*\n", " ", text)  # 合并反斜杠续行
    pins: dict[str, tuple[str, set[str]]] = {}
    for line in filter(None, (ln.strip() for ln in text.splitlines())):
        head, *options = line.split()
        name, sep, version = head.partition("==")
        assert sep, f"requirements.txt 条目未用 == 钉版本: {line!r}"
        hashes = {opt.removeprefix("--hash=sha256:") for opt in options if opt.startswith("--hash=sha256:")}
        assert len(hashes) == len(options), f"{name} 含 --hash=sha256 以外的选项: {options}"
        pins[name] = (version, hashes)
    return pins


def test_requirements_pin_versions_and_the_hash_of_every_vendored_wheel():
    expected = {package: (version, {sha for name, sha in EXPECTED_WHEELS.items() if name.startswith(f"{package}-{version}-")}) for package, version in EXPECTED_PINS.items()}
    assert _requirement_pins() == expected


# ── 入库产物：BPE 缓存 ──────────────────────────────────────────────────────


def test_bpe_cache_file_is_named_by_the_url_sha1_tiktoken_uses_as_cache_key():
    assert [p.name for p in BPE_DIR.iterdir()] == [hashlib.sha1(O200K_URL.encode()).hexdigest()]


def test_bpe_cache_file_matches_the_hash_tiktoken_embeds_for_o200k_base():
    (cache_file,) = BPE_DIR.iterdir()
    assert hashlib.sha256(cache_file.read_bytes()).hexdigest() == O200K_SHA256


def test_bpe_cache_is_marked_binary_so_git_keeps_bytes_exact_and_review_skips_its_199998_line_diff():
    """BPE 缓存是 19.9 万行纯文本：`* text=auto eol=lf` 会让 git 把它当文本做换行归一化（改字节即破坏 sha256），
    且区内文本文件会整份进 AI 评审（rule.json 对 docker/sandbox/cache/** 的说明）。binary = -diff -merge -text。"""
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "docker/sandbox/cache/tiktoken/* binary" in attributes


def test_vendored_bpe_cache_gives_exact_o200k_counts_without_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """沙箱里 execution-context 做的那件事：TIKTOKEN_CACHE_DIR 指向 vendoring 的缓存、断网，仍能得到精确计数。

    缓存先拷到 tmp_path：tiktoken 对哈希不符的缓存文件会 os.remove——不能让失败的测试删掉入库文件。
    """
    import tiktoken
    import tiktoken.load as tiktoken_load
    from tiktoken_ext import openai_public

    cache = tmp_path / "tiktoken-cache"
    shutil.copytree(BPE_DIR, cache)
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache))

    def _no_network(blobpath: str) -> bytes:
        raise AssertionError(f"tiktoken 试图联网下载 {blobpath}——vendoring 的 BPE 缓存没被命中")

    monkeypatch.setattr(tiktoken_load, "read_file", _no_network)

    encoding = tiktoken.Encoding(**openai_public.o200k_base())

    assert encoding.name == "o200k_base"
    assert encoding.encode("hello world") == [24912, 2375]


# ── Dockerfile 接线 ────────────────────────────────────────────────────────


def _dockerfile_instructions() -> list[str]:
    """Dockerfile 逻辑指令：续行已合并，注释与空行已剔除。"""
    instructions: list[str] = []
    pending = ""
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1].rstrip() + " "
        else:
            instructions.append(pending + stripped)
            pending = ""
    return instructions


def _copies() -> dict[str, str]:
    """COPY 源 → 目标。"""
    copies: dict[str, str] = {}
    for instruction in _dockerfile_instructions():
        match = re.fullmatch(r"COPY\s+(\S+)\s+(\S+)", instruction)
        if match:
            copies[match.group(1)] = match.group(2)
    return copies


def _vendored_install_instruction() -> str:
    matches = [i for i in _dockerfile_instructions() if i.startswith("RUN") and "--require-hashes" in i]
    assert len(matches) == 1, "应恰有一条 RUN 以 --require-hashes 离线安装 vendoring wheel"
    return matches[0]


def test_dockerfile_copies_vendored_wheels_and_bpe_cache_from_the_offline_cache_dir():
    copies = _copies()
    assert WHEEL_SOURCE in copies, "Dockerfile 未 COPY vendoring 的 python-wheels"
    assert BPE_SOURCE in copies, "Dockerfile 未 COPY 预烘焙的 tiktoken BPE 缓存"


def test_dockerfile_points_tiktoken_cache_dir_at_the_copied_bpe_cache():
    env = re.search(r"^ENV\s+TIKTOKEN_CACHE_DIR=(\S+)\s*$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert env, "Dockerfile 未设 ENV TIKTOKEN_CACHE_DIR——运行期 tiktoken 会去 Azure 下载 BPE"
    assert env.group(1) == _copies()[BPE_SOURCE].rstrip("/")


def test_dockerfile_installs_vendored_wheels_offline_with_hash_pinning():
    install = _vendored_install_instruction()
    wheel_dest = _copies()[WHEEL_SOURCE].rstrip("/")
    for required in (
        "--network=none",  # 构建本身即证明安装不依赖网络
        "--no-index",
        "--no-deps",  # requests 由基座预装
        "--require-hashes",
        f"--find-links {wheel_dest}",
        f"-r {wheel_dest}/requirements.txt",
        "--break-system-packages",
    ):
        assert required in install, f"离线安装指令缺 {required!r}"
    assert not re.search(r"--index-url|--extra-index-url|\s-i\s", install), "构建期不得显式指定包索引"


def test_dockerfile_smoke_checks_the_offline_o200k_encoding_during_build():
    install = _vendored_install_instruction()
    assert "tiktoken.get_encoding('o200k_base')" in install
    assert "[24912, 2375]" in install


def test_dockerfile_installs_vendored_wheels_after_pip_is_upgraded():
    """--break-system-packages 需要 pip>=23.0.1，基座自带 22.x——必须排在 pip 自升级之后。"""
    instructions = _dockerfile_instructions()
    upgrade = next(i for i, ins in enumerate(instructions) if "--upgrade pip" in ins)
    install = next(i for i, ins in enumerate(instructions) if "--require-hashes" in ins)
    assert upgrade < install
