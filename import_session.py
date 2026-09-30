#!/usr/bin/env python3
"""把清华问卷的登录凭据从剪贴板/HAR 装进本地会话文件。

接受三种剪贴板内容：DevTools“以 cURL 格式复制”的整条命令、Cookie 请求头的值、
或单独的 sessionid 值。凭据只在本机流转：不打印、不上传、不作为命令行参数出现。
用法见 README 的“登录只在本机接入”一节。
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thuwj import BASE_URL, Error, STATE, import_har, origin, private_json  # noqa: E402

NAME_OK = re.compile(r"^[A-Za-z0-9_.\-]+$")
# RFC 6265 cookie-octet：不含空白、控制字符、双引号、逗号、分号、反斜杠。
BARE_VALUE_OK = re.compile(r"^[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]{16,}$")
CURL_CALL = re.compile(r"^\s*(?:/[^\s]+/)?curl(?:\s|$)")
CURL_VALUE_OPTIONS = {
    "-H", "--header", "-b", "--cookie", "--url", "-X", "--request",
    "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
    "-e", "--referer", "-A", "--user-agent", "-o", "--output",
}
CURL_FLAGS = {
    "-s", "-S", "-sS", "--silent", "--show-error", "-f", "--fail",
    "--fail-with-body", "-L", "--location", "-k", "--insecure",
    "-i", "--include", "-g", "--globoff", "--compressed", "--path-as-is",
    "--http1.1", "--http2", "--http2-prior-knowledge",
}


def curl_words(text):
    """只做 POSIX 词法解码；不执行 shell、不展开变量或命令替换。"""
    # shlex 不删除 shell 的反斜杠换行；先仅在单引号之外处理续行。
    logical, quote, index = [], None, 0
    while index < len(text):
        char = text[index]
        if char == "\\" and quote != "'" and index + 1 < len(text):
            if text[index + 1] == "\n":
                index += 2
                continue
            if text[index + 1:index + 3] == "\r\n":
                index += 3
                continue
            logical.extend(text[index:index + 2])
            index += 2
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        logical.append(char)
        index += 1
    try:
        words = shlex.split("".join(logical), posix=True)
    except ValueError as exc:
        raise Error("INVALID_CURL", "cURL 引号或转义不完整；请重新复制 POSIX cURL") from exc
    if not words or Path(words[0]).name != "curl":
        raise Error("INVALID_CURL", "只接受一条独立的 POSIX curl 命令")
    return words


def clipboard_text():
    try:
        done = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Error("NO_CLIPBOARD", "无法读取剪贴板；改用管道：pbpaste | python3 import_session.py --stdin") from exc
    if done.returncode or not done.stdout.strip():
        raise Error("NO_CLIPBOARD", "剪贴板为空；请先复制 Cookie 请求头的值")
    return done.stdout.strip()


def looks_like_har(text):
    if not text.lstrip().startswith("{"):
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and isinstance(data.get("log"), dict)


def looks_like_curl(text):
    return bool(CURL_CALL.search(text))


def extract_from_curl(text):
    """校验唯一请求来源，只提取实际 Cookie 参数，不执行或重放请求。"""
    words = curl_words(text)
    urls, cookie_chunks, index = [], [], 1
    while index < len(words):
        word = words[index]
        index += 1
        option, value = word, None
        if word.startswith("--") and "=" in word:
            option, value = word.split("=", 1)
        elif word not in CURL_VALUE_OPTIONS:
            for short in ("-H", "-b", "-X", "-d", "-e", "-A", "-o"):
                if word.startswith(short) and len(word) > 2:
                    option, value = short, word[2:]
                    break
        if option in CURL_VALUE_OPTIONS:
            if value is None:
                if index == len(words):
                    raise Error("INVALID_CURL", "cURL 选项缺少参数；请重新复制")
                value = words[index]
                index += 1
            if option == "--url":
                urls.append(value)
            elif option in ("-b", "--cookie"):
                if "=" not in value:
                    raise Error("INVALID_CURL", "只支持内联 Cookie 串，不读取 cURL 的 Cookie 文件")
                cookie_chunks.append(value)
            elif option in ("-H", "--header"):
                name, separator, content = value.partition(":")
                if separator and name.strip().lower() == "cookie":
                    cookie_chunks.append(content.strip())
        elif word in CURL_FLAGS:
            continue
        elif word.startswith("-"):
            raise Error("INVALID_CURL", "存在不支持的 cURL 选项；请复制浏览器的 POSIX cURL 或 Cookie 值")
        else:
            urls.append(word)
    if len(urls) != 1:
        raise Error("INVALID_CURL", "cURL 必须包含唯一且明确的请求 URL")
    try:
        matches = origin(urls[0]) == origin(BASE_URL)
    except (Error, ValueError):
        matches = False
    if not matches:
        raise Error("ORIGIN_MISMATCH", "cURL 请求必须为清华问卷站点的 HTTPS 同源地址")
    if len(cookie_chunks) > 1:
        raise Error("AMBIGUOUS_COOKIE", "cURL 含多个 Cookie 来源；请只复制实际发送的 Cookie 请求头值")
    chunk = cookie_chunks[0] if cookie_chunks else ""
    if any(ord(char) < 32 or ord(char) == 127 for char in chunk):
        raise Error("INVALID_CURL", "Cookie 参数含控制字符；请重新复制")
    # 保留完整值供 parse_cookies 处理；同名 Cookie 取串中的最后一个。
    return chunk


def parse_cookies(text):
    cleaned = text.strip()
    if "\n" in cleaned:
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        picked = [line for line in lines if re.match(r"(?i)^cookie\s*:", line)] or \
                 [line for line in lines if "=" in line]
        cleaned = "; ".join(picked) if picked else cleaned
    cleaned = re.sub(r"(?i)^cookie\s*:\s*", "", cleaned.strip())
    if "=" not in cleaned:
        if BARE_VALUE_OK.fullmatch(cleaned):
            return {"sessionid": cleaned}
        raise Error("UNRECOGNIZED_INPUT", "剪贴板内容既不是 Cookie 串也不是 sessionid 值")
    cookies = {}
    for pair in re.split(r"[;\n]", cleaned):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key, value = key.strip(), value.strip()
        # 原样保留值：浏览器实际发送的 current_rid="" 就是两个引号字符，不能替它脱引号。
        if NAME_OK.fullmatch(key):
            cookies[key] = value
    return cookies


def install(cookies, output):
    if not cookies.get("sessionid"):
        raise Error("HAR_NO_SESSION",
                    "没有找到 sessionid；请复制完整的 Cookie 请求头（含 sessionid 与 _xsrf）")
    private_json(output, {"origin": BASE_URL, "cookies": cookies, "xsrf_header": ""})
    return {"ok": True, "session_file": str(Path(output).resolve()),
            "cookie_names": sorted(cookies), "has_xsrf": "_xsrf" in cookies,
            "note": "凭据未打印；导入成功不代表会话有效，下一步运行 doctor 验证"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--har", help="含凭据的 HAR 文件（可选；不导入 HAR 时读取剪贴板）")
    parser.add_argument("--stdin", action="store_true", help="从标准输入读取而非剪贴板")
    parser.add_argument("--session-file", default=str(STATE / "session.json"))
    args = parser.parse_args(argv)
    output = Path(args.session_file)
    if args.har:
        result = import_har(args.har, output)
        state = json.loads(output.read_text(encoding="utf-8"))
        result = {"ok": True, "session_file": result["session_file"],
                  "cookie_names": sorted(state["cookies"]),
                  "has_xsrf": "_xsrf" in state["cookies"],
                  "note": "凭据未打印；下一步运行 doctor 验证会话"}
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return
    text = sys.stdin.read() if args.stdin or not sys.stdin.isatty() else clipboard_text()
    if not text.strip():
        raise Error("NO_INPUT", "没有读到内容；请复制 Cookie 值后重试")
    if looks_like_har(text):
        with tempfile.NamedTemporaryFile("w", suffix=".har", encoding="utf-8", delete=False) as tmp:
            tmp.write(text)
            temporary = tmp.name
        try:
            result = import_har(temporary, output)
        finally:
            Path(temporary).unlink()
        state = json.loads(output.read_text(encoding="utf-8"))
        print(json.dumps({"ok": True, "session_file": result["session_file"],
                          "cookie_names": sorted(state["cookies"]),
                          "has_xsrf": "_xsrf" in state["cookies"],
                          "note": "凭据未打印；下一步运行 doctor 验证会话"},
                         ensure_ascii=False, separators=(",", ":")))
        return
    if looks_like_curl(text):
        # DevTools“以 cURL 格式复制”：只取 cookie，其余请求参数丢弃。
        text = extract_from_curl(text)
        if not text:
            raise Error("UNRECOGNIZED_INPUT",
                        "cURL 里没有可用的 cookie；该请求可能不带登录态，换一条发往 wenjuan.tsinghua.edu.cn 的请求")
    print(json.dumps(install(parse_cookies(text), output), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Error as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc)}},
                         ensure_ascii=False, separators=(",", ":")))
        sys.exit(2)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "LOCAL_INPUT_OR_SCHEMA_ERROR",
                      "message": "本地文件或输入异常；" + type(exc).__name__}},
                         ensure_ascii=False, separators=(",", ":")))
        sys.exit(2)
