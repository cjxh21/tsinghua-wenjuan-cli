import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from thuwj import BASE_URL, Client, Error, Response, import_har, main, normalize, private_json, sign_header, sign_params


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def send(self, *args):
        self.calls.append(args)
        return self.responses.pop(0)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.session = self.work / "session.json"
        self.signer = self.work / "signer.json"
        private_json(self.session, {"origin": BASE_URL, "cookies": {"sessionid": "TEST_SESSION", "_xsrf": "TEST_XSRF"}})
        private_json(self.signer, {"params": {"APP_KEY": "demo", "SECRET": "seed", "SITE": "wenjuan_web"},
                                   "header": {"APP_KEY": "demo2", "SECRET": "seed2"}})

    def tearDown(self):
        self.temp.cleanup()

    def client(self, responses=()):
        transport = FakeTransport(responses)
        return Client(self.session, self.signer, transport), transport

    def test_native_signer_coercion_and_stale_metadata(self):
        profile = {"APP_KEY": "demo", "SECRET": "seed", "SITE": "wenjuan_web"}
        signed = sign_params({"z": [1, None, 3], "a": False, "object": {"x": 1},
                              "signature": "expired", "timestamp": 0}, profile, 42)
        canonical = "falsedemo[object Object]42wenjuan_web1,,3seed"
        self.assertEqual(signed["signature"], hashlib.md5(canonical.encode()).hexdigest())
        self.assertEqual(signed["timestamp"], 42)

    def test_header_signature_preserves_native_unicode_json(self):
        actual = sign_header({"b": True, "a": 2}, {"title": "田径"}, {"APP_KEY": "demo2", "SECRET": "seed2"})
        expected = hashlib.md5('{"title":"田径"}2trueseed2'.encode()).hexdigest()
        self.assertEqual(actual, "wj demo2:" + expected)

    def test_null_in_param_signer_uses_array_join_not_literal_null(self):
        profile = {"APP_KEY": "demo", "SECRET": "seed", "SITE": "wenjuan_web"}
        signed = sign_params({"a": None}, profile, 42)
        self.assertEqual(signed["signature"], hashlib.md5(b"demo42wenjuan_webseed").hexdigest())

    def test_http_200_auth_error_is_not_success(self):
        response = Response(200, {}, b'{"err_code":10001,"err_msg":"auth","status_code":0}')
        with self.assertRaises(Error) as ctx:
            normalize(response)
        self.assertEqual(ctx.exception.code, "AUTH_REQUIRED")

    def test_other_business_error_is_not_success(self):
        with self.assertRaises(Error) as ctx:
            normalize(Response(200, {}, b'{"err_code":123,"status_code":0}'))
        self.assertEqual(ctx.exception.code, "BUSINESS_ERROR")

    def test_write_preview_does_not_need_auth_or_network(self):
        client, transport = self.client()
        self.session.unlink()
        self.signer.unlink()
        result = client.call("project.rename", "0" * 24, body={"title": "preview"})
        self.assertTrue(result["dry_run"])
        self.assertEqual(transport.calls, [])
        self.assertNotIn("preview", json.dumps(result))

    def test_non_get_read_uses_form_and_cookie_without_apply(self):
        client, transport = self.client([Response(200, {}, b'{"err_code":0,"status_code":1,"data":{}}')])
        result = client.call("project.count", body={"proj_id": "0" * 24, "scene_type": "survey"})
        self.assertTrue(result["ok"])
        method, url, headers, body = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(parse_qs(body.decode())["scene_type"], ["survey"])
        self.assertNotIn("__FORMDATA__", parse_qs(body.decode()))
        self.assertIn("sessionid=TEST_SESSION", headers["Cookie"])

    def test_apply_create_without_native_payload_rejected_before_network(self):
        client, transport = self.client()
        with self.assertRaises(Error) as ctx:
            client.call("project.create", apply=True)
        self.assertEqual(ctx.exception.code, "MISSING_NATIVE_PAYLOAD")
        self.assertEqual(transport.calls, [])

    def test_cross_origin_api_rejected_before_transport(self):
        client, transport = self.client()
        with self.assertRaises(Error):
            client.request("GET", "//other.example/api/")
        self.assertEqual(transport.calls, [])

    def test_missing_session_fails_before_bootstrap(self):
        client, transport = self.client()
        self.session.unlink()
        self.signer.unlink()
        with self.assertRaises(Error) as ctx:
            client.call("project.list")
        self.assertEqual(ctx.exception.code, "SESSION_NOT_CONFIGURED")
        self.assertEqual(transport.calls, [])

    def test_cdn_redirect_never_receives_site_credentials(self):
        client, transport = self.client([
            Response(302, {"location": "https://files.example.org/export.xlsx?download=1"}, b""),
            Response(200, {"content-type": "application/octet-stream"}, b"PK\x03\x04test-export"),
        ])
        out = self.work / "result.xlsx"
        result = client.download_url(BASE_URL + "/export-link", out)
        self.assertTrue(result["ok"])
        self.assertIn("Cookie", transport.calls[0][2])
        self.assertEqual(transport.calls[1][2], {})
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)

    def test_insecure_download_redirect_rejected(self):
        client, transport = self.client([Response(302, {"location": "http://files.example.org/export.xlsx"}, b"")])
        with self.assertRaises(Error):
            client.download_url(BASE_URL + "/export-link", self.work / "result.xlsx")
        self.assertEqual(len(transport.calls), 1)

    def test_login_html_not_written_as_excel(self):
        client, _ = self.client([Response(200, {"content-type": "text/html"}, b"<html>login</html>")])
        out = self.work / "result.xlsx"
        with self.assertRaises(Error):
            client.download_url(BASE_URL + "/export", out)
        self.assertFalse(out.exists())

    def test_download_does_not_overwrite_previous_results(self):
        client, _ = self.client([Response(200, {}, b"new export")])
        out = self.work / "result.csv"
        out.write_text("original")
        with self.assertRaises(FileExistsError):
            client.download_url(BASE_URL + "/export", out)
        self.assertEqual(out.read_text(), "original")

    def test_contract_defaults_merge_and_caller_wins(self):
        # 列表接口必带筛选集；调用者显式给的值覆盖默认值（2026-09-30 实测依据）。
        client, _ = self.client()
        plan = client.call("project.list", params={"page_size": 2}, dry_run=True)
        self.assertTrue({"p_type", "status", "time_order", "create_type", "plugin", "search_txt"} <=
                        set(plan["parameter_names"]))
        self.assertIn("page_size", plan["parameter_names"])
        self.assertEqual(plan["parameter_names"].count("page_size"), 1)
        check = client.call("project.publish_check", dry_run=True, project="0123456789abcdef01234567")
        self.assertEqual(check["path"], "/edit/ajax/publish_check/0123456789abcdef01234567/")

        client, transport = self.client([Response(200, {}, b'{"status_code":1,"data":{}}')])
        client.call("project.list", params={"page_size": 2, "status": "custom"})
        actual = parse_qs(urlsplit(transport.calls[0][1]).query, keep_blank_values=True)
        self.assertEqual(actual["page_size"], ["2"])
        self.assertEqual(actual["status"], ["custom"])
        self.assertEqual(actual["search_txt"], [""])
        self.assertEqual(actual["p_type"], ["all"])

    def test_doctor_rejects_unrelated_and_malformed_success_responses(self):
        good = {"project_list": [], "total_count": 0, "total_page": 0}
        bad = [None, {"unrelated": "FAKE_PRIVATE_DATA"},
               {**good, "project_list": {}}, {**good, "project_list": ["bad"]}]
        for key in ("project_list", "total_count", "total_page"):
            bad.append({k: v for k, v in good.items() if k != key})
        for key in ("total_count", "total_page"):
            for value in (True, -1, "0", 0.5, None):
                bad.append({**good, key: value})
        for payload in bad:
            response = Response(200, {}, json.dumps({"status_code": 1, "data": payload}).encode())
            client, _ = self.client([response])
            with self.subTest(payload=payload), patch("thuwj.Client", return_value=client):
                with self.assertRaises(Error) as caught:
                    main(["doctor"])
                self.assertEqual(caught.exception.code, "UNEXPECTED_RESPONSE")
                self.assertNotIn("FAKE_PRIVATE_DATA", str(caught.exception))

    def test_doctor_accepts_empty_account_and_sends_native_filters(self):
        response = Response(200, {}, b'{"status_code":1,"data":{"project_list":[],"total_count":0,"total_page":0}}')
        client, transport = self.client([response])
        with patch("thuwj.Client", return_value=client):
            result = main(["doctor"])
        self.assertTrue(result["session_valid"])
        self.assertEqual(result["project_total"], 0)
        actual = parse_qs(urlsplit(transport.calls[0][1]).query, keep_blank_values=True)
        self.assertEqual(actual["page_size"], ["2"])
        self.assertEqual(actual["page"], ["1"])
        self.assertTrue({"p_type", "status", "time_order", "create_type", "plugin", "search_txt"} <= set(actual))

    def test_doctor_uses_native_project_total_not_page_length_or_answers(self):
        payload = {"project_list": [{"rspd_count": 99}], "total_count": 5, "total_page": 3}
        client, _ = self.client([Response(200, {}, json.dumps({"status_code": 1, "data": payload}).encode())])
        with patch("thuwj.Client", return_value=client):
            result = main(["doctor"])
        self.assertEqual(result["project_total"], 5)

    def test_anonymous_doctor_does_not_confirm_a_user_session(self):
        response = Response(200, {}, b'{"status_code":1,"data":{"project_list":[],"total_count":0,"total_page":0}}')
        client, transport = self.client([response])
        with patch("thuwj.Client", return_value=client):
            result = main(["doctor", "--anonymous"])
        self.assertFalse(result["session_valid"])
        self.assertNotIn("Cookie", transport.calls[0][2])

    def test_har_import_only_uses_target_origin_and_private_permissions(self):
        har = self.work / "capture.har"
        har.write_text(json.dumps({"log": {"entries": [
            {"request": {"url": BASE_URL + "/api/project/list/", "cookies": [{"name": "sessionid", "value": "LOCAL_ONLY"}]}},
            {"request": {"url": "https://other.example/list", "cookies": [{"name": "sessionid", "value": "FOREIGN"}]}},
        ]}}))
        result = import_har(har, self.session)
        self.assertNotIn("LOCAL_ONLY", json.dumps(result))
        self.assertEqual(json.loads(self.session.read_text())["cookies"]["sessionid"], "LOCAL_ONLY")
        self.assertEqual(self.session.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
