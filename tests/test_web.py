import http.client
import json
import os
import queue
import stat
import tempfile
import time
import unittest

import support  # noqa: F401
from support import reset_settings
from tvkit import control, settings, web


def tearDownModule():
    reset_settings()


class FakeHost:
    name = "Test TV"

    def __init__(self):
        self.running = None
        self.started, self.changed, self.confirms, self.system_calls = [], [], [], []
        self.hidden = []

    def apps(self):
        return [{"id": "clock", "name": "CLOCK", "desc": "", "icon": "clock", "version": "1.0.0", "source": "builtin", "hidden": "clock" in self.hidden}]

    def known_app_ids(self):
        return {"clock", "news"}

    def status(self):
        return {"app": "launcher", "screen": "MENU", "playing": None, "text_input": False, "running": self.running}

    def foreground(self):
        return self.running["id"] if self.running else "launcher"

    def start_app(self, app_id):
        self.started.append(app_id)
        return app_id == "clock"

    def settings_values(self):
        return {f["key"]: f["default"] for f in settings.SCHEMA}

    def settings_changed(self, keys):
        self.changed.append(keys)

    def confirm(self, title, lines, action):
        self.confirms.append((title, lines))
        return self.server.confirmations.request(title, lines, action)

    def system(self, action):
        self.system_calls.append(action)

    def log_tail(self, n):
        return ["line one", "line two"]


class Remote:
    """A tiny browser: keeps the cookie, sends the headers the real page sends."""

    def __init__(self, port, host=None):
        self.port, self.cookie, self.host = port, None, host

    def call(self, method, path, body=None, headers=None, raw=None, host=None, origin=None, tvl=True, ctype="application/json"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host or self.host or f"127.0.0.1:{self.port}"}
        if method == "POST":
            if tvl:
                h["X-TVL"] = "1"
            if ctype:
                h["Content-Type"] = ctype
        if self.cookie:
            h["Cookie"] = "tvl=" + self.cookie
        if origin:
            h["Origin"] = origin
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None))
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        payload = r.read()
        sc = r.getheader("Set-Cookie")
        if sc and sc.startswith("tvl="):
            self.cookie = sc.split(";")[0][4:]
        try:
            obj = json.loads(payload)
        except ValueError:
            obj = payload
        c.close()
        return r.status, obj, r


class WebCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.now = [1000.0]
        self.host = FakeHost()
        self.devices = web.Devices(os.path.join(self.tmp, "devices.json"))
        self.pairing = web.Pairing(clock=lambda: self.now[0])
        self.confirmations = web.Confirmations(clock=lambda: self.now[0])
        os.environ["TVL_SETTINGS"] = os.path.join(self.tmp, "settings.json")
        self.server = web.WebServer(self.host, port=0, bind="127.0.0.1", devices=self.devices, pairing=self.pairing, confirmations=self.confirmations)
        self.host.server = self.server
        self.server.start()
        self.addCleanup(self.server.stop)
        self.r = Remote(self.server.port)

    def pair(self, remote=None):
        remote = remote or self.r
        pin, _ = self.pairing.open()
        code, obj, _ = remote.call("POST", "/api/pair", {"pin": pin, "name": "Test phone"})
        self.assertEqual(code, 200, obj)
        return remote


class PairingTests(WebCase):
    def test_pairing_flow_sets_a_strict_cookie_and_stores_only_a_hash(self):
        pin, _ = self.pairing.open()
        code, obj, resp = self.r.call("POST", "/api/pair", {"pin": pin, "name": "Remko's phone"})
        self.assertEqual(code, 200)
        cookie = resp.getheader("Set-Cookie")
        for part in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(part, cookie)
        token = self.r.cookie
        self.assertGreaterEqual(len(token), 40)
        stored = open(self.devices.path).read()
        self.assertNotIn(token, stored)
        self.assertEqual(stat.S_IMODE(os.stat(self.devices.path).st_mode), 0o600)
        self.assertEqual(self.r.call("GET", "/api/session")[1], {"paired": True, "name": "Remko's phone", "server": "Test TV"})

    def test_the_pin_works_once_and_expires(self):
        pin, _ = self.pairing.open()
        self.assertEqual(self.r.call("POST", "/api/pair", {"pin": pin})[0], 200)
        self.assertEqual(Remote(self.server.port).call("POST", "/api/pair", {"pin": pin})[0], 403)           # used up
        pin, _ = self.pairing.open()
        self.now[0] += web.PIN_TTL_S + 1
        self.assertEqual(Remote(self.server.port).call("POST", "/api/pair", {"pin": pin})[0], 403)

    def test_wrong_and_missing_pins_and_closed_pairing(self):
        other = Remote(self.server.port)
        self.assertEqual(other.call("POST", "/api/pair", {"pin": "123456"})[0], 403)                     # nothing open on the TV
        self.pairing.open()
        for bad in ({"pin": "000000"}, {"pin": 123456}, {"pin": None}, {}, {"pin": ["1"]}):
            code, obj, _ = other.call("POST", "/api/pair", bad)
            self.assertIn(code, (403, 429), bad)
        self.assertIsNone(other.cookie)

    def test_per_address_rate_limit_even_with_the_right_pin(self):
        pin, _ = self.pairing.open()
        codes = [self.r.call("POST", "/api/pair", {"pin": "999999" if pin != "999999" else "111111"})[0] for _ in range(web.PIN_TRIES)]
        self.assertEqual(set(codes), {403})
        code, obj, _ = self.r.call("POST", "/api/pair", {"pin": pin})
        self.assertEqual(code, 429)                                                                       # blocked: no oracle for the right code
        self.now[0] += web.PIN_WINDOW_S + 1
        self.pairing.open()
        self.assertEqual(self.r.call("POST", "/api/pair", {"pin": self.pairing.pin})[0], 200)

    def test_lockout_after_many_failures_and_new_pin_needed(self):
        pin, _ = self.pairing.open()
        for i in range(web.LOCK_AFTER):
            self.pairing.attempt(f"10.0.0.{i}", "000000" if pin != "000000" else "111111")            # many addresses: the per-address limit does not apply
        self.assertEqual(self.pairing.attempt("10.9.9.9", pin), "locked")
        self.assertIsNone(self.pairing.pin)
        self.now[0] += web.LOCK_S + 1
        self.assertEqual(self.pairing.attempt("10.9.9.9", pin), "closed")                             # the old PIN is dead for good
        pin2, _ = self.pairing.open()
        self.assertEqual(self.pairing.attempt("10.9.9.9", pin2), "ok")

    def test_pins_are_six_digits_and_random(self):
        pins = {self.pairing.open()[0] for _ in range(20)}
        self.assertTrue(all(len(p) == 6 and p.isdigit() for p in pins))
        self.assertGreater(len(pins), 15)


class AuthTests(WebCase):
    PROTECTED = [("GET", "/api/status"), ("GET", "/api/apps"), ("GET", "/api/settings"), ("GET", "/api/devices"), ("GET", "/api/log"), ("GET", "/api/confirm/abc"),
                 ("POST", "/api/key"), ("POST", "/api/text"), ("POST", "/api/home"), ("POST", "/api/apps/start"), ("POST", "/api/settings"),
                 ("POST", "/api/devices/revoke"), ("POST", "/api/system"), ("POST", "/api/logout")]

    def test_every_api_route_refuses_an_unpaired_caller(self):
        for method, path in self.PROTECTED:
            self.assertEqual(self.r.call(method, path, {})[0], 401, path)
        self.assertEqual(self.host.started, [])
        self.assertEqual(self.host.confirms, [])

    def test_a_forged_or_truncated_cookie_is_refused(self):
        self.pair()
        good = self.r.cookie
        for bad in ("x", good[:-1], good + "a", "", "A" * 500):
            r = Remote(self.server.port)
            r.cookie = bad
            self.assertEqual(r.call("GET", "/api/status")[0], 401)

    def test_hosts_that_are_not_this_machine_are_refused_against_dns_rebinding(self):
        self.pair()
        for host in ("evil.example.com", "evil.example.com:8090", "attacker.net", "a.b.c.d", "192.168.1.1.evil.com", "localhost.evil.com"):
            self.assertEqual(self.r.call("GET", "/api/status", host=host)[0], 403, host)
            self.assertEqual(self.r.call("GET", "/", host=host)[0], 403, host)
        for host in ("127.0.0.1:8090", "localhost:8090", "tv.local", "[::1]:8090", "192.168.1.169:8090"):
            self.assertEqual(self.r.call("GET", "/api/status", host=host)[0], 200, host)

    def test_cross_site_origins_are_refused_and_no_cors_is_offered(self):
        self.pair()
        self.assertEqual(self.r.call("POST", "/api/key", {"name": "up"}, origin="http://evil.example")[0], 403)
        self.assertEqual(self.r.call("GET", "/api/status", origin="http://evil.example")[0], 403)
        own = f"http://127.0.0.1:{self.server.port}"
        self.assertEqual(self.r.call("GET", "/api/status", origin=own)[0], 200)
        code, obj, resp = self.r.call("OPTIONS", "/api/key", {})
        self.assertEqual(code, 405)
        self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))

    def test_posts_need_the_custom_header_and_json(self):
        self.pair()
        self.assertEqual(self.r.call("POST", "/api/key", {"name": "up"}, tvl=False)[0], 403)
        self.assertEqual(self.r.call("POST", "/api/key", {"name": "up"}, ctype="text/plain")[0], 400)
        self.assertEqual(self.r.call("POST", "/api/key", {"name": "up"}, ctype=None)[0], 400)

    def test_bad_bodies(self):
        self.pair()
        self.assertEqual(self.r.call("POST", "/api/key", raw=b"{not json")[0], 400)
        self.assertEqual(self.r.call("POST", "/api/key", raw=b"[1]")[0], 400)
        self.assertEqual(self.r.call("POST", "/api/text", raw=b" " * (web.MAX_BODY + 10))[0], 413)
        self.assertEqual(self.r.call("POST", "/api/key", headers={"Content-Length": "abc"}, raw=b"{}")[0], 400)

    def test_security_headers_on_pages_and_api(self):
        for path in ("/", "/app.js", "/app.css", "/api/session"):
            code, _, resp = self.r.call("GET", path)
            self.assertEqual(code, 200, path)
            self.assertIn("script-src 'self'", resp.getheader("Content-Security-Policy"))
            self.assertNotIn("unsafe-inline", resp.getheader("Content-Security-Policy"))
            self.assertEqual(resp.getheader("Cache-Control"), "no-store")
            self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
            self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")
        self.assertNotIn("Server", [k for k, _ in self.r.call("GET", "/")[2].getheaders() if "python" in _.lower()])

    def test_the_page_never_uses_innerhtml_or_inline_script(self):
        _, js, _ = self.r.call("GET", "/app.js")
        _, html, _ = self.r.call("GET", "/")
        self.assertNotIn(b"innerHTML", js)
        self.assertNotIn(b"eval(", js)
        self.assertNotIn(b"<script>", html)
        self.assertNotIn(b"onclick=", html)

    def test_the_page_uses_no_inline_styles_because_the_csp_blocks_them(self):
        import re
        _, js, _ = self.r.call("GET", "/app.js")
        _, html, _ = self.r.call("GET", "/")
        self.assertIsNone(re.search(rb"style: '|style: \"|setAttribute\('style'|style=\"|cssText", js + html))
        self.assertIn(b"Object.assign(e.style", js)                                     # widths are set through the DOM's style object instead

    def test_unknown_paths_and_traversal(self):
        for path in ("/nope", "/../etc/passwd", "/app.js/../../x", "/api", "/api/unknown"):
            self.assertIn(self.r.call("GET", path)[0], (401, 403, 404), path)
        self.pair()
        self.assertEqual(self.r.call("GET", "/api/unknown")[0], 404)

    def test_logout_and_revoke_end_the_session(self):
        self.pair()
        other = self.pair(Remote(self.server.port))
        devices = self.r.call("GET", "/api/devices")[1]["devices"]
        self.assertEqual(len(devices), 2)
        mine = next(d for d in devices if d["this"])
        theirs = next(d for d in devices if not d["this"])
        self.assertEqual(self.r.call("POST", "/api/devices/revoke", {"id": theirs["id"]})[0], 200)
        self.assertEqual(other.call("GET", "/api/status")[0], 401)
        self.assertEqual(self.r.call("POST", "/api/devices/revoke", {"id": "nonexistent"})[0], 404)
        self.assertEqual(self.r.call("POST", "/api/logout", {})[0], 200)
        self.assertEqual(self.r.call("GET", "/api/status")[0], 401)
        self.assertEqual(len(self.devices.list()), 0)

    def test_a_restart_of_the_server_keeps_paired_phones(self):
        self.pair()
        again = web.Devices(self.devices.path)
        self.assertIsNotNone(again.check(self.r.cookie))

    def test_the_device_table_is_bounded(self):
        for i in range(web.MAX_DEVICES + 5):
            self.devices.add(f"p{i}")
        self.assertEqual(len(self.devices.list()), web.MAX_DEVICES)


class ControlTests(WebCase):
    def test_keys_text_and_home_reach_the_foreground_app_through_its_socket(self):
        class Shell:
            q = queue.Queue()

            def status(self):
                return {"app": "clock", "screen": "CLOCK", "playing": {"title": "x"}, "text_input": True}
        launcher_shell, app_shell = Shell(), Shell()
        launcher_shell.q, app_shell.q = queue.Queue(), queue.Queue()
        ls, a = control.ControlServer(launcher_shell, "launcher"), control.ControlServer(app_shell, "clock")
        ls.start(); a.start()
        self.addCleanup(ls.stop); self.addCleanup(a.stop)
        self.pair()
        self.assertEqual(self.r.call("POST", "/api/key", {"name": "down"})[0], 200)                      # no app running: the menu gets it
        self.assertEqual(launcher_shell.q.get(timeout=1), ("cmd", "down"))
        self.host.running = {"id": "clock", "name": "CLOCK"}
        self.assertEqual(self.r.call("POST", "/api/key", {"name": "enter"})[0], 200)
        self.assertEqual(app_shell.q.get(timeout=1), ("cmd", "enter"))
        self.assertEqual(self.r.call("POST", "/api/text", {"text": "hi there"})[0], 200)
        self.assertEqual([app_shell.q.get(timeout=1)[1] for _ in range(8)], ["char:h", "char:i", "char:space", "char:t", "char:h", "char:e", "char:r", "char:e"])
        self.assertEqual(self.r.call("POST", "/api/home", {})[0], 200)
        self.assertEqual(app_shell.q.get(timeout=1), ("cmd", "quit"))
        st = self.r.call("GET", "/api/status")[1]
        self.assertEqual((st["launcher"]["running"]["id"], st["app"]["screen"], st["app"]["text_input"]), ("clock", "CLOCK", True))

    def test_unknown_keys_are_refused_before_they_reach_any_socket(self):
        self.pair()
        for bad in ("rm", "char:a", "", None, 7, ["up"], "UP"):
            self.assertEqual(self.r.call("POST", "/api/key", {"name": bad})[0], 400, bad)
        self.assertEqual(self.r.call("POST", "/api/text", {"text": 5})[0], 400)

    def test_a_key_when_the_app_is_not_listening_says_so(self):
        self.pair()
        self.host.running = {"id": "ghost", "name": "GHOST"}
        self.assertEqual(self.r.call("POST", "/api/key", {"name": "up"})[0], 409)


class ActionTests(WebCase):
    def test_apps_and_start(self):
        self.pair()
        self.assertEqual(self.r.call("GET", "/api/apps")[1]["apps"][0]["id"], "clock")
        self.assertEqual(self.r.call("POST", "/api/apps/start", {"id": "clock"})[0], 200)
        self.assertEqual(self.r.call("POST", "/api/apps/start", {"id": "ghost"})[0], 404)
        self.assertEqual(self.r.call("POST", "/api/apps/start", {"id": 5})[0], 404)
        self.assertEqual(self.host.started, ["clock", "ghost"])

    def test_settings_are_validated_saved_and_announced(self):
        self.pair()
        code, obj, _ = self.r.call("GET", "/api/settings")
        self.assertEqual(code, 200)
        self.assertTrue(any(f["key"] == "clock.h24" for f in obj["schema"]))
        code, obj, _ = self.r.call("POST", "/api/settings", {"changes": {"clock.h24": False, "launcher.hidden": ["clock"]}})
        self.assertEqual(code, 200, obj)
        self.assertEqual(self.host.changed, [["clock.h24", "launcher.hidden"]])
        self.assertEqual(settings.load_flat()["clock.h24"], False)

    def test_bad_settings_are_refused_with_a_reason_and_save_nothing(self):
        self.pair()
        for bad in ({"changes": {"clock.h24": "yes"}}, {"changes": {"nope": 1}}, {"changes": {}}, {"changes": "x"}, {}, {"changes": {"launcher.hidden": ["ghost"]}},
                    {"changes": {"feeds": [{"name": "x", "url": "file:///etc/passwd"}]}}):
            code, obj, _ = self.r.call("POST", "/api/settings", bad)
            self.assertEqual(code, 400, bad)
            self.assertTrue(obj["error"])
        self.assertEqual(self.host.changed, [])
        self.assertFalse(os.path.exists(settings.path()))

    def test_system_actions_wait_for_the_tv(self):
        self.pair()
        code, obj, _ = self.r.call("POST", "/api/system", {"action": "poweroff"})
        self.assertEqual(code, 202)
        cid = obj["confirm"]
        self.assertEqual(self.host.confirms[0][0], "Shut down this device?")
        self.assertEqual(self.r.call("GET", "/api/confirm/" + cid)[1]["state"], "pending")
        self.assertEqual(self.host.system_calls, [])                                    # nothing happens on the phone's word alone
        self.assertEqual(self.confirmations.current()["id"], cid)
        self.assertTrue(self.confirmations.answer(cid, True))
        for _ in range(50):
            if self.host.system_calls:
                break
            time.sleep(0.02)
        self.assertEqual(self.host.system_calls, ["poweroff"])
        self.assertEqual(self.r.call("GET", "/api/confirm/" + cid)[1]["state"], "allowed")
        self.assertFalse(self.confirmations.answer(cid, True))                          # an answer settles it only once

    def test_denied_and_expired_confirmations_do_nothing(self):
        self.pair()
        cid = self.r.call("POST", "/api/system", {"action": "restart"})[1]["confirm"]
        self.assertTrue(self.confirmations.answer(cid, False))
        cid2 = self.r.call("POST", "/api/system", {"action": "restart"})[1]["confirm"]
        self.now[0] += web.CONFIRM_TTL_S + 1
        self.assertIsNone(self.confirmations.current())
        self.assertEqual(self.r.call("GET", "/api/confirm/" + cid2)[1]["state"], "expired")
        self.assertFalse(self.confirmations.answer(cid2, True))
        time.sleep(0.1)
        self.assertEqual(self.host.system_calls, [])
        self.assertEqual(self.r.call("GET", "/api/confirm/nonexistent")[0], 404)

    def test_unknown_system_actions_are_refused(self):
        self.pair()
        for bad in ("format", "", None, "RESTART"):
            self.assertEqual(self.r.call("POST", "/api/system", {"action": bad})[0], 400)
        self.assertEqual(self.host.confirms, [])

    def test_log_and_a_crashing_host_do_not_take_the_server_down(self):
        self.pair()
        self.assertEqual(self.r.call("GET", "/api/log")[1]["lines"], ["line one", "line two"])
        self.host.apps = lambda: 1 / 0
        self.assertEqual(self.r.call("GET", "/api/apps")[0], 500)
        self.assertEqual(self.r.call("GET", "/api/session")[0], 200)


class HelperTests(unittest.TestCase):
    def test_lan_ip_returns_an_address(self):
        self.assertRegex(web.lan_ip(), r"^\d+\.\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
