#!/usr/bin/env python3
"""公开仓库前的凭据自检：确保会话、HAR、Cookie 值不会被提交或推送。

用法：
  python3 check_no_secrets.py            # 检查仓库全部跟踪文件（CI 用）
  python3 check_no_secrets.py --staged   # 只检查暂存内容（pre-commit 用）
退出码 1 表示发现疑似凭据或不该入库的文件；命令只报告文件与行号，不打印命中的值。
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NEVER_TRACKED = (".state/", "exports/")
NEVER_TRACKED_PATTERNS = (re.compile(r"\.har$", re.I), re.compile(r"^session.*\.json$", re.I),
                          re.compile(r"\.pyc$", re.I), re.compile(r"(^|/)\.env", re.I))
PATTERNS = (
    ("疑似会话 ID", re.compile(r"sessionid\s*=\s*[A-Za-z0-9_\-|%.]{16,}", re.I)),
    ("疑似 Cookie 请求头", re.compile(r"(?:^|[^-])cookie\s*:\s*\S{16,}", re.I)),
    ("疑似 XSRF 令牌", re.compile(r"(?:_xsrf|xsrf-token)\s*[=:]\s*[A-Za-z0-9_\-%.]{16,}", re.I)),
    ("疑似 Bearer/签名凭据", re.compile(r"authorization\s*:\s*(?:bearer|wj)\s+\S{16,}", re.I)),
)
# 测试与文档里允许出现合成值；命中这些标记的一律不算凭据。
SYNTHETIC = re.compile(r"FAKE|TEST|EXAMPLE|SYNTHETIC|DEMO|PLACEHOLDER|DUMMY|XXXX", re.I)
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".xlsx", ".zip", ".woff", ".woff2"}
MAX_BYTES = 2_000_000


def git(*args):
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return done.returncode, done.stdout


def candidate_files(staged):
    if staged:
        code, out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
        if code != 0:
            return None
        return [p for p in out.split("\0") if p]
    code, out = git("ls-files", "-z")
    if code == 0:
        return [p for p in out.split("\0") if p]
    # 不是 git 仓库：退化为扫描目录，但仍排除 .state/exports。
    return [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
            and not any(str(p.relative_to(ROOT)).startswith(prefix) for prefix in NEVER_TRACKED)]


def forbidden(path):
    if any(path.startswith(prefix) for prefix in NEVER_TRACKED):
        return "不应入库的目录（会话/导出）"
    for pattern in NEVER_TRACKED_PATTERNS:
        if pattern.search(path):
            return "不应入库的文件类型（HAR/会话/密钥）"
    return ""


def scan(path):
    findings = []
    target = ROOT / path
    reason = forbidden(path)
    if reason:
        findings.append((path, 0, reason))
        return findings
    if not target.is_file() or target.suffix.lower() in SKIP_SUFFIX or target.stat().st_size > MAX_BYTES:
        return findings
    try:
        text = target.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return findings
    for number, line in enumerate(text.splitlines(), 1):
        for label, pattern in PATTERNS:
            match = pattern.search(line)
            if match and not SYNTHETIC.search(match.group(0)):
                findings.append((path, number, label))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="只检查 git 暂存内容")
    args = parser.parse_args(argv)
    files = candidate_files(args.staged)
    if files is None:
        print("check_no_secrets: 不是 git 仓库，也无法读取暂存区；请先把仓库初始化", file=sys.stderr)
        return 1
    findings = [item for path in files for item in scan(path)]
    if findings:
        print("发现 %d 处问题，禁止提交/推送：" % len(findings), file=sys.stderr)
        for path, number, label in findings:
            location = "%s:%d" % (path, number) if number else path
            print("  %s  %s" % (location, label), file=sys.stderr)
        print("凭据只应留在本机被忽略的 .state/session.json；不要粘贴到任何被跟踪的文件。", file=sys.stderr)
        return 1
    print("check_no_secrets: %d 个文件通过，未发现会话、HAR 或 Cookie 值" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
