#!/usr/bin/env python3
"""import_session.py 的离线测试：不联网、不读取真实凭据。"""
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import import_session as helper
from thuwj import Error

FAKE = "sessionid=FAKE1234567890abcdef1234567890ab; _xsrf=FAKEXSRF"


class ImportSessionTests(unittest.TestCase):
    def test_cookie_header_parsed(self):
        cookies = helper.parse_cookies(FAKE)
        self.assertEqual(cookies["sessionid"], "FAKE1234567890abcdef1234567890ab")
        self.assertIn("_xsrf", cookies)

    def test_bare_sessionid_value_accepted(self):
        self.assertEqual(helper.parse_cookies("FAKE1234567890abcdef1234567890ab"),
                         {"sessionid": "FAKE1234567890abcdef1234567890ab"})

    def test_bare_site_sessionid_with_pipes(self):
        value = "FAKE_USER|1700000003|FAKE_SIGNATURE123456789"
        self.assertEqual(helper.parse_cookies(value), {"sessionid": value})

    def test_bare_value_cannot_inject_cookie_boundaries(self):
        for value in ("FAKE1234567890abcdef;other", "FAKE1234567890\nabcdef",
                      "FAKE1234567890\tabcdef", 'FAKE1234567890"abcdef'):
            with self.subTest(value=value), self.assertRaises(Error):
                helper.parse_cookies(value)

    def test_multiline_request_headers_pick_cookie_line(self):
        raw = "GET /api/project/list/ HTTP/1.1\nCookie: " + FAKE + "\nAccept: */*\n"
        cookies = helper.parse_cookies(raw)
        self.assertIn("sessionid", cookies)
        self.assertNotIn("Accept", cookies)

    def test_missing_sessionid_is_rejected(self):
        with self.assertRaises(Error) as caught:
            helper.install({"_xsrf": "only"}, Path(tempfile.mkdtemp()) / "s.json")
        self.assertEqual(caught.exception.code, "HAR_NO_SESSION")

    def test_session_file_is_private_and_site_scoped(self):
        with tempfile.TemporaryDirectory() as work:
            target = Path(work) / "session.json"
            result = helper.install(helper.parse_cookies(FAKE), target)
            mode = stat.S_IMODE(os.stat(target).st_mode)
            stored = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(mode, 0o600)
            self.assertEqual(stored["origin"], helper.BASE_URL)
            self.assertNotIn("FAKE1234567890abcdef", json.dumps(result))

    def test_har_detection(self):
        self.assertTrue(helper.looks_like_har('{"log":{"entries":[]}}'))
        self.assertFalse(helper.looks_like_har(FAKE))
        self.assertFalse(helper.looks_like_har('{"log": "not-an-object"}'))

    def test_curl_clipboard_with_cookie_header(self):
        curl = ("curl 'https://wenjuan.tsinghua.edu.cn/api/project/list/?page=1&appkey=sqjDmXVd5LNYf9r4' \\\n"
                "  -H 'accept: application/json' \\\n"
                "  -H 'cookie: " + FAKE + "' \\\n"
                "  -H 'user-agent: Mozilla/5.0'")
        self.assertTrue(helper.looks_like_curl(curl))
        cookies = helper.parse_cookies(helper.extract_from_curl(curl))
        self.assertEqual(cookies["sessionid"], "FAKE1234567890abcdef1234567890ab")
        self.assertIn("_xsrf", cookies)
        self.assertNotIn("user-agent", cookies)
        self.assertNotIn("page", cookies)

    def test_curl_clipboard_with_cookie_flag_keeps_values(self):
        curl = 'curl -s "https://wenjuan.tsinghua.edu.cn/" -b "sessionid=FAKE1234567890abcdef1234567890ab"'
        cookies = helper.parse_cookies(helper.extract_from_curl(curl))
        self.assertEqual(cookies, {"sessionid": "FAKE1234567890abcdef1234567890ab"})

    def test_chrome_shape_embedded_quotes_and_last_sessionid_wins(self):
        # 真实 Chrome 的 -b 串：值里含字面双引号，且有 3 个同名 sessionid。
        curl = (
            "curl --url 'https://wenjuan.tsinghua.edu.cn/api/project/list/?p_type=all&appkey=FAKEKEY' \\\n"
            "  -H 'accept: application/json, text/plain, */*' \\\n"
            "  -b 'sessionid=FAKEOLD1|1700000001|aaa; _za=zzz; origin_referer=default; "
            "sessionid=FAKEOLD2|1700000002|bbb; already_set_login_status=1; current_rid=\"\"; "
            "open_rspd_link_tag_X=\"\"; lookback_answer_ids=%5B%5B%22fake%22%5D%5D; "
            "sessionid=FAKELAST|1700000003|ccc; _zvt=1700000001123.1700000003456' \\\n"
            "  -H 'user-agent: Mozilla/5.0 (X11; Linux aarch64) Edg/154'"
        )
        cookies = helper.parse_cookies(helper.extract_from_curl(curl))
        self.assertEqual(cookies["sessionid"], "FAKELAST|1700000003|ccc")
        self.assertEqual(cookies["current_rid"], '""')
        self.assertNotIn("user-agent", cookies)
        self.assertNotIn("accept", cookies)
        self.assertNotIn("FAKEOLD1", json.dumps(cookies))

    def test_curl_unquoted_cookie_argument(self):
        curl = "curl https://wenjuan.tsinghua.edu.cn/ -b sessionid=FAKEBARE1234567890abcdef"
        cookies = helper.parse_cookies(helper.extract_from_curl(curl))
        self.assertEqual(cookies, {"sessionid": "FAKEBARE1234567890abcdef"})

    def test_curl_rejects_other_origins(self):
        for url in ("https://foreign.example/api/list", "http://wenjuan.tsinghua.edu.cn/",
                    "https://wenjuan.tsinghua.edu.cn.evil.example/",
                    "https://wenjuan.tsinghua.edu.cn:444/",
                    "https://user@wenjuan.tsinghua.edu.cn/"):
            with self.subTest(url=url):
                with self.assertRaises(Error) as caught:
                    helper.extract_from_curl("curl " + shlex.quote(url) + " -b " + shlex.quote(FAKE))
                self.assertNotIn("FAKE123", str(caught.exception))

    def test_curl_cookie_not_extracted_from_url_or_body(self):
        for command in (
            "curl 'https://wenjuan.tsinghua.edu.cn/?sessionid=FAKE_QUERY123456789'",
            "curl 'https://wenjuan.tsinghua.edu.cn/' --data-raw 'sessionid=FAKE_BODY123456789'",
        ):
            with self.subTest(command=command):
                self.assertEqual(helper.extract_from_curl(command), "")

    def test_curl_double_quote_escapes_keep_complete_cookie(self):
        cookie = 'sessionid=FAKE_OLD123456789012; current_rid=""; sessionid=FAKE_LAST123456789012'
        quoted = '"' + cookie.replace('"', '\\"') + '"'
        command = "curl 'https://wenjuan.tsinghua.edu.cn/' -b " + quoted
        self.assertEqual(shlex.split(command)[-1], cookie)
        cookies = helper.parse_cookies(helper.extract_from_curl(command))
        self.assertEqual(cookies["sessionid"], "FAKE_LAST123456789012")
        self.assertEqual(cookies["current_rid"], '""')

    def test_curl_cookie_option_forms_and_default_https_port(self):
        for option in ("-b " + shlex.quote(FAKE), "--cookie " + shlex.quote(FAKE),
                       "--cookie=" + shlex.quote(FAKE), "-b" + shlex.quote(FAKE),
                       "--header " + shlex.quote("Cookie: " + FAKE),
                       "--header=" + shlex.quote("Cookie: " + FAKE),
                       "-H" + shlex.quote("Cookie: " + FAKE)):
            command = "curl --url=https://wenjuan.tsinghua.edu.cn:443/list/ " + option
            with self.subTest(option=option):
                self.assertEqual(helper.parse_cookies(helper.extract_from_curl(command)),
                                 helper.parse_cookies(FAKE))

    def test_curl_mixed_cookie_sources_are_rejected(self):
        command = ("curl 'https://wenjuan.tsinghua.edu.cn/' -b " + shlex.quote(FAKE) +
                   " -H 'Cookie: sessionid=FAKE_OTHER123456789'")
        with self.assertRaises(Error):
            helper.extract_from_curl(command)

    def test_curl_ambiguous_or_unsupported_input_is_rejected(self):
        for command in (
            "curl 'https://wenjuan.tsinghua.edu.cn/' 'https://foreign.example/' -b " + shlex.quote(FAKE),
            "curl 'https://wenjuan.tsinghua.edu.cn/' -b 'unterminated",
            "curl 'https://wenjuan.tsinghua.edu.cn/' --cookie @cookies.txt",
            "curl -b " + shlex.quote(FAKE),
            "curl --config hidden-curl-config -b " + shlex.quote(FAKE),
        ):
            with self.subTest(command=command), self.assertRaises(Error):
                helper.extract_from_curl(command)

    def test_cli_foreign_curl_preserves_existing_session_without_leaking(self):
        with tempfile.TemporaryDirectory() as work:
            target = Path(work) / "session.json"
            helper.install(helper.parse_cookies(FAKE), target)
            before = target.read_bytes()
            done = subprocess.run(
                [sys.executable, str(Path(helper.__file__).resolve()), "--stdin",
                 "--session-file", str(target)],
                input="curl 'https://foreign.example/' -b 'sessionid=FAKE_FOREIGN123456789'",
                text=True, capture_output=True, timeout=10)
            self.assertEqual(done.returncode, 2)
            self.assertFalse(json.loads(done.stdout)["ok"])
            self.assertNotIn("FAKE_FOREIGN", done.stdout + done.stderr)
            self.assertEqual(target.read_bytes(), before)

    def test_cli_curl_is_parsed_without_command_execution(self):
        with tempfile.TemporaryDirectory() as work:
            target, marker = Path(work) / "session.json", Path(work) / "marker"
            command = ("curl 'https://wenjuan.tsinghua.edu.cn/' -b " + shlex.quote(FAKE) +
                       " -H " + shlex.quote("X-Demo: $(touch " + str(marker) + ")"))
            done = subprocess.run(
                [sys.executable, str(Path(helper.__file__).resolve()), "--stdin",
                 "--session-file", str(target)],
                input=command, text=True, capture_output=True, timeout=10)
            self.assertEqual(done.returncode, 0, done.stdout)
            self.assertEqual(json.loads(target.read_text())["cookies"], helper.parse_cookies(FAKE))
            self.assertFalse(marker.exists())
            self.assertNotIn("FAKE123", done.stdout + done.stderr)

    def test_curl_without_cookie_is_rejected(self):
        curl = "curl 'https://wenjuan.tsinghua.edu.cn/' -H 'accept: application/json'"
        self.assertEqual(helper.extract_from_curl(curl), "")


if __name__ == "__main__":
    unittest.main()
