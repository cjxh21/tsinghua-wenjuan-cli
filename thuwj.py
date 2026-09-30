#!/usr/bin/env python3
"""Local CLI/SDK for evidenced Tsinghua Wenjuan web endpoints. Python >= 3.9."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlencode, urljoin, urlsplit

BASE_URL = "https://wenjuan.tsinghua.edu.cn"
ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".state"


class Error(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_object(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise Error("INVALID_INPUT", "JSON 必须是对象")
    return data


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(compact(value) + "\n")
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def origin(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise Error("INVALID_URL", "仅接受无内嵌用户名、密码的 HTTPS 地址")
    return (parts.scheme, parts.hostname.lower(), parts.port or 443)


def js_string(value):
    """JavaScript String coercion used by the native request signer."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "[object Object]"
    if isinstance(value, list):
        return ",".join("" if x is None else js_string(x) for x in value)
    return str(value)


def sign_params(values, profile, timestamp=None):
    data = dict(values)
    # Never reuse an expired signature copied from a captured request.
    for key in ("signature", "appkey", "web_site", "timestamp"):
        data.pop(key, None)
    data.update(appkey=profile["APP_KEY"], web_site=profile["SITE"],
                timestamp=int(time.time() * 1000) if timestamp is None else timestamp)
    # Native code collects values in an array then joins: null contributes "".
    plain = "".join("" if data[k] is None else js_string(data[k])
                    for k in sorted(data) if k != "secret")
    data["signature"] = hashlib.md5((plain + profile["SECRET"]).encode("utf-8")).hexdigest()
    return data


def sign_header(params, body, profile):
    values = "".join(js_string(params[k]) for k in sorted(params))
    digest = hashlib.md5(((compact(body) if body is not None else "") + values +
                          profile["SECRET"]).encode("utf-8")).hexdigest()
    return "wj " + profile["APP_KEY"] + ":" + digest


def extract_profiles(bundle):
    # Parse literal public frontend profiles; do not evaluate vendor JavaScript.
    blocks = re.findall(r'\{APP_KEY:"([^"\\]+)",SECRET:"([^"\\]+)"(?:,SITE:"([^"\\]+)")?\}', bundle)
    query = next((dict(APP_KEY=a, SECRET=s, SITE=site) for a, s, site in blocks
                  if site == "wenjuan_web"), None)
    header = next((dict(APP_KEY=a, SECRET=s) for a, s, site in blocks if not site), None)
    if not query or not header or 'headers.Authorization="wj ' not in bundle:
        raise Error("FRONTEND_CHANGED", "公开前端的签名格式已变化，需要重新核对")
    return {"params": query, "header": header,
            "sha256": hashlib.sha256(bundle.encode("utf-8")).hexdigest()}


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class CurlTransport:
    """Use OS curl TLS trust. Credentials are passed through stdin, never argv."""
    def send(self, method, url, headers=None, body=None):
        origin(url)
        with tempfile.TemporaryDirectory(prefix="thuwj-") as work:
            work = Path(work)
            output = work / "response.bin"
            header_file = work / "headers.txt"
            settings = {"url": url, "request": method, "output": str(output),
                        "dump-header": str(header_file), "write-out": "%{http_code}",
                        "max-time": "90", "connect-timeout": "15", "max-redirs": "0"}
            config = [key + " = " + compact(value) for key, value in settings.items()]
            config += ["silent", "show-error"]
            for key, value in (headers or {}).items():
                if "\n" in value or "\r" in value:
                    raise Error("INVALID_HEADER", "请求头包含换行")
                config.append("header = " + compact(key + ": " + value))
            if body is not None:
                body_file = work / "request.bin"
                body_file.write_bytes(body)
                body_file.chmod(0o600)
                config.append("data-binary = " + compact("@" + str(body_file)))
            try:
                result = subprocess.run(["curl", "--config", "-"], input="\n".join(config),
                                        text=True, capture_output=True, timeout=95)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise Error("TRANSPORT_ERROR", "curl 不可用或请求超时") from exc
            if result.returncode:
                # curl diagnostics may contain signed URLs. Never echo them.
                raise Error("TRANSPORT_ERROR", "网络或 TLS 请求失败；curl 退出码 " + str(result.returncode))
            chunks = header_file.read_text(encoding="latin1").strip().split("\n\n")
            parsed = {}
            for line in chunks[-1].splitlines()[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    parsed[key.lower()] = value.strip()
            return Response(int(result.stdout), parsed, output.read_bytes())


def normalize(response):
    if response.status in (301, 302, 303, 307, 308, 401, 403):
        raise Error("AUTH_OR_PERMISSION_REQUIRED", "登录失效、权限不足或接口发生重定向")
    if not 200 <= response.status < 300:
        raise Error("HTTP_ERROR", "服务器 HTTP 状态 " + str(response.status))
    try:
        data = json.loads(response.body)
    except (ValueError, UnicodeError) as exc:
        raise Error("NOT_JSON", "接口返回非 JSON，可能为登录页或接口版本已变化") from exc
    if not isinstance(data, dict):
        raise Error("UNEXPECTED_RESPONSE", "服务器响应结构不符合预期")
    code = data.get("err_code", 0)
    failed = str(code) not in ("0", "None", "") or data.get("status_code") in (0, "0")
    failed = failed or bool(data.get("error_msg")) or isinstance(data.get("err_msg"), str) and bool(data["err_msg"])
    if failed:
        if str(code) == "10001":
            raise Error("AUTH_REQUIRED", "清华问卷系统要求有效登录会话（业务错误 10001）")
        # Do not echo arbitrary server messages that can include request/credential data.
        raise Error("BUSINESS_ERROR", "服务器拒绝请求；业务错误码 " + str(code))
    return data


class Client:
    def __init__(self, session_file=None, signer_file=None, transport=None):
        self.session_file = Path(session_file or STATE / "session.json")
        self.signer_file = Path(signer_file or STATE / "signer.json")
        self.transport = transport or CurlTransport()
        self.contracts = read_object(ROOT / "contracts.json")

    def bootstrap(self, bundle_file=None):
        if bundle_file:
            bundle = Path(bundle_file).read_text(encoding="utf-8")
            source = "local_public_bundle"
        else:
            home = self.transport.send("GET", BASE_URL + "/")
            if home.status != 200:
                raise Error("BOOTSTRAP_FAILED", "公开首页无法读取")
            scripts = re.findall(r'<script\b[^>]*src=["\']([^"\']+)["\']', home.body.decode("utf-8"))
            bundle = None
            for src in reversed(scripts):
                url = urljoin(BASE_URL + "/", src)
                if origin(url) != origin(BASE_URL) or not urlsplit(url).path.startswith("/_nuxt_homesite/"):
                    continue
                response = self.transport.send("GET", url)
                if response.status != 200:
                    continue
                candidate = response.body.decode("utf-8")
                try:
                    extract_profiles(candidate)
                except Error:
                    continue
                bundle, source = candidate, url
                break
            if bundle is None:
                raise Error("FRONTEND_CHANGED", "未找到可识别的公开签名模块")
        profiles = extract_profiles(bundle)
        profiles.update(source=source, fetched_at=int(time.time()))
        private_json(self.signer_file, profiles)
        return {"ok": True, "source": source, "sha256": profiles["sha256"],
                "note": "仅缓存公开前端签名配置，不代表已登录"}

    def credentials(self, anonymous=False):
        if anonymous:
            return {}, {}
        if not self.session_file.exists():
            raise Error("SESSION_NOT_CONFIGURED", "尚未导入清华站点登录会话，见 README 的登录接入步骤")
        state = read_object(self.session_file)
        if state.get("origin") != BASE_URL or not isinstance(state.get("cookies"), dict) or not state["cookies"]:
            raise Error("INVALID_SESSION", "登录文件缺少该站点的 cookies")
        headers = {"Cookie": "; ".join(k + "=" + v for k, v in state["cookies"].items())}
        token = state["cookies"].get("XSRF-TOKEN") or state.get("xsrf_header")
        if token:
            headers["X-XSRF-TOKEN"] = unquote(token)
        return state["cookies"], headers

    def call(self, name, project=None, params=None, body=None, apply=False, dry_run=False, anonymous=False):
        if name not in self.contracts:
            raise Error("UNKNOWN_OPERATION", "未知操作；先运行 capabilities")
        item = self.contracts[name]
        path = item["path"]
        if "{project}" in path:
            if not project or not re.fullmatch(r"[0-9a-fA-F]{24}", project):
                raise Error("INVALID_PROJECT", "project 必须为清华系统的 24 位项目 ID")
            path = path.replace("{project}", project)
        params, body = dict(params or {}), None if body is None else dict(body)
        if item.get("defaults"):
            # 已核对的默认参数（例如列表接口必带的筛选集）；调用者显式给的值优先。
            merged = dict(item["defaults"])
            if item["method"] == "GET":
                merged.update(params)
                params = merged
            else:
                merged.update(body or {})
                body = merged
        values = params if item["method"] == "GET" else (body or {})
        for key in item.get("required", []):
            if key not in values:
                raise Error("MISSING_PARAMETER", "缺少参数 " + key)
        plan = {"ok": True, "dry_run": True, "operation": name, "method": item["method"],
                "path": path, "effect": item["effect"], "parameter_names": sorted(params),
                "body_field_names": sorted(body or {}), "signer": item["signer"],
                "evidence": item["evidence"], "authenticated_verified": False}
        if dry_run or item["effect"] != "read" and not apply:
            return plan
        if item["effect"] != "read" and not values:
            raise Error("MISSING_NATIVE_PAYLOAD", "执行写入或创建导出任务前必须提供已核对的原生请求参数")
        return self.request(item["method"], path, params, body, item["signer"],
                            item.get("encoding", "json"), anonymous=anonymous)

    def request(self, method, path, params=None, body=None, signer="params", encoding="json", anonymous=False):
        url = urljoin(BASE_URL + "/", path)
        if not path.startswith("/") or origin(url) != origin(BASE_URL):
            raise Error("ORIGIN_MISMATCH", "API 请求必须留在清华问卷站点")
        if urlsplit(url).query or urlsplit(url).fragment:
            raise Error("INVALID_PATH", "查询参数请通过 params JSON 提供，path 不应包含查询内容或片段")
        cookies, headers = self.credentials(anonymous)
        if not self.signer_file.exists():
            self.bootstrap()
        profiles = read_object(self.signer_file)
        params = dict(params or {})
        body = None if method == "GET" else dict(body or {})
        if body is not None and encoding == "form":
            body["__FORMDATA__"] = True
        if signer == "params":
            if method == "GET":
                params = sign_params(params, profiles["params"])
            else:
                body = sign_params(body, profiles["params"])
        elif signer == "header":
            headers["Authorization"] = sign_header(params, body, profiles["header"])
        else:
            raise Error("INVALID_SIGNER", "仅支持 params 或 header 签名")
        if params:
            # axios uses JavaScript coercion; keys ending [] need explicit captured names.
            url += ("&" if "?" in url else "?") + urlencode({k: js_string(v) for k, v in params.items()})
        payload = None
        if body is not None:
            body.pop("__FORMDATA__", None)
            if body.pop("__XSRF__", False) and cookies.get("_xsrf"):
                body["_xsrf"] = cookies["_xsrf"]
            if encoding == "form":
                headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
                payload = urlencode({k: js_string(v) for k, v in body.items()}).encode("utf-8")
            else:
                headers["Content-Type"] = "application/json;charset=UTF-8"
                payload = compact(body).encode("utf-8")
        headers.update(Accept="application/json", Referer=BASE_URL + "/list/", Origin=BASE_URL)
        data = normalize(self.transport.send(method, url, headers, payload))
        return {"ok": True, "http_status": 200, "response": data}

    def download_task(self, output, page=1, index=1):
        result = self.call("downloads.list", params={"page": page, "page_size": 20})
        tasks = result["response"].get("data", {}).get("task_list")
        if not isinstance(tasks, list) or not 1 <= index <= len(tasks):
            raise Error("INVALID_TASK", "当前页没有该序号的下载任务")
        task = tasks[index - 1]
        if task.get("status") != 1 or not task.get("download_url"):
            raise Error("TASK_NOT_READY", "任务未就绪或已过期")
        return self.download_url(task["download_url"], output)

    def download_url(self, url, output):
        url = urljoin(BASE_URL + "/", url)
        for _ in range(6):
            target_origin = origin(url)
            # CDN downloads are allowed, but never receive the site's session or signer.
            headers = self.credentials()[1] if target_origin == origin(BASE_URL) else {}
            response = self.transport.send("GET", url, headers)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise Error("INVALID_DOWNLOAD", "下载重定向缺少目标")
                url = urljoin(url, location)
                continue
            if response.status != 200 or not response.body:
                raise Error("INVALID_DOWNLOAD", "下载未返回有效文件")
            if "text/html" in response.headers.get("content-type", "") or response.body.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                raise Error("INVALID_DOWNLOAD", "下载返回了 HTML 页面，未保存为结果文件")
            if "application/json" in response.headers.get("content-type", "") and Path(output).suffix.lower() != ".json":
                raise Error("INVALID_DOWNLOAD", "下载返回 JSON，可能是错误信息，未保存为表格")
            output = Path(output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".thuwj-", delete=False) as tmp:
                temporary = Path(tmp.name)
                os.fchmod(tmp.fileno(), 0o600)
                tmp.write(response.body)
            try:
                os.link(temporary, output)  # atomic, refuses to overwrite an existing export
            finally:
                temporary.unlink()
            return {"ok": True, "path": str(output), "bytes": len(response.body),
                    "sha256": hashlib.sha256(response.body).hexdigest()}
        raise Error("INVALID_DOWNLOAD", "下载重定向过多")


def import_har(har_file, output):
    har = read_object(har_file)
    for entry in reversed(har.get("log", {}).get("entries", [])):
        request = entry.get("request", {})
        try:
            if origin(request.get("url", "")) != origin(BASE_URL):
                continue
        except Error:
            continue
        cookies = {x["name"]: x["value"] for x in request.get("cookies", [])
                   if isinstance(x.get("name"), str) and isinstance(x.get("value"), str)}
        headers = {x["name"].lower(): x["value"] for x in request.get("headers", [])}
        for pair in headers.get("cookie", "").split(";"):
            if "=" in pair:
                key, value = pair.strip().split("=", 1)
                cookies[key] = value
        if cookies.get("sessionid"):
            private_json(output, {"origin": BASE_URL, "cookies": cookies,
                                  "xsrf_header": headers.get("x-xsrf-token", "")})
            return {"ok": True, "session_file": str(Path(output).resolve()),
                    "note": "仅导入该站点凭据；尚未验证会话有效性"}
    raise Error("HAR_NO_SESSION", "HAR 缺少该站点 sessionid；浏览器默认的脱敏 HAR 通常不包含登录凭据")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-file", default=str(STATE / "session.json"))
    parser.add_argument("--signer-file", default=str(STATE / "signer.json"))
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("capabilities")
    bootstrap = subs.add_parser("bootstrap")
    bootstrap.add_argument("--bundle-file")
    auth = subs.add_parser("auth")
    auth.add_argument("action", choices=["status", "import-har"])
    auth.add_argument("--har")
    doctor = subs.add_parser("doctor")
    doctor.add_argument("--anonymous", action="store_true")
    call = subs.add_parser("call")
    call.add_argument("operation")
    call.add_argument("--project")
    call.add_argument("--params-file")
    call.add_argument("--body-file")
    call.add_argument("--apply", action="store_true")
    call.add_argument("--dry-run", action="store_true")
    request = subs.add_parser("request", help="对已核对的原生接口重放请求；默认只输出计划")
    request.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    request.add_argument("path")
    request.add_argument("--params-file")
    request.add_argument("--body-file")
    request.add_argument("--signer", choices=["params", "header"], default="params")
    request.add_argument("--encoding", choices=["json", "form"], default="json")
    request.add_argument("--apply", action="store_true")
    download = subs.add_parser("download")
    download.add_argument("--page", type=int, default=1)
    download.add_argument("--index", type=int, default=1, help="当前下载列表页的任务序号，从 1 开始")
    download.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    client = Client(args.session_file, args.signer_file)
    if args.command == "capabilities":
        return {"ok": True, "origin": BASE_URL, "operations": client.contracts,
                "authenticated_verified": False, "question_crud": "requires_captured_editor_contract"}
    if args.command == "bootstrap":
        return client.bootstrap(args.bundle_file)
    if args.command == "auth":
        if args.action == "status":
            return {"ok": True, "configured": client.session_file.exists(), "validated": False}
        if not args.har:
            raise Error("INVALID_INPUT", "import-har 需要 --har")
        return import_har(args.har, client.session_file)
    if args.command == "doctor":
        # page_size=1 会被服务端以 HTTP 422 拒绝（2026-09-30 实测，最小 2）。
        result = client.call("project.list", params={"page": 1, "page_size": 2}, anonymous=args.anonymous)
        payload = result["response"].get("data")
        if not isinstance(payload, dict) or \
                not isinstance(payload.get("project_list"), list) or \
                not all(isinstance(item, dict) for item in payload["project_list"]) or \
                any(type(payload.get(key)) is not int or payload[key] < 0
                    for key in ("total_count", "total_page")):
            raise Error("UNEXPECTED_RESPONSE", "问卷列表返回结构不符合契约，无法确认会话有效")
        total = payload["total_count"]
        return {"ok": True, "session_valid": not args.anonymous, "project_total": total,
                "note": "只验证列表查询；未修改任何问卷", "response_keys": list(result["response"])}
    if args.command in ("call", "request"):
        params = read_object(args.params_file) if args.params_file else {}
        body = read_object(args.body_file) if args.body_file else None
        if args.command == "call":
            return client.call(args.operation, args.project, params, body, args.apply, args.dry_run)
        if not args.apply:
            url = urljoin(BASE_URL + "/", args.path)
            if not args.path.startswith("/") or origin(url) != origin(BASE_URL):
                raise Error("ORIGIN_MISMATCH", "原生请求只能发往清华问卷站点")
            if urlsplit(url).query or urlsplit(url).fragment:
                raise Error("INVALID_PATH", "查询参数请通过 --params-file 提供")
            return {"ok": True, "dry_run": True, "method": args.method, "path": args.path,
                    "parameter_names": sorted(params), "body_field_names": sorted(body or {}),
                    "note": "原生接口及参数由调用者核对；未发送网络请求"}
        return client.request(args.method, args.path, params, body, args.signer, args.encoding)
    return client.download_task(args.out, args.page, args.index)


if __name__ == "__main__":
    try:
        print(compact(main()))
    except Error as exc:
        print(compact({"ok": False, "error": {"code": exc.code, "message": str(exc)}}))
        sys.exit(2)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(compact({"ok": False, "error": {"code": "LOCAL_INPUT_OR_SCHEMA_ERROR",
                     "message": "本地文件、参数或响应结构异常；" + type(exc).__name__}}))
        sys.exit(2)
