"""The local Link exchange server — the only network-facing surface.

Binds loopback, serves the page, and accepts exactly one exchange on a
nonce path with a Host check. A wrong Host or wrong path must 403; a valid
POST must capture the payload and release the waiter.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from sara.link import ExchangeServer, render_page


@pytest.fixture()
def server():
    srv = ExchangeServer(render_page("ally", "tok-test", "nonce123", False), "nonce123")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _url(path: str) -> str:
    return f"http://127.0.0.1:8484{path}"


def test_serves_the_page_on_loopback(server: ExchangeServer) -> None:
    with urllib.request.urlopen(_url("/")) as resp:
        body = resp.read().decode()
    assert "Let's link" in body and "tok-test" in body


def test_wrong_host_header_is_403(server: ExchangeServer) -> None:
    req = urllib.request.Request(_url("/"), headers={"Host": "evil.example:8484"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403


def test_wrong_nonce_path_is_403(server: ExchangeServer) -> None:
    req = urllib.request.Request(_url("/exchange/wrong"), data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403


def test_valid_exchange_captures_payload_and_signals(server: ExchangeServer) -> None:
    req = urllib.request.Request(
        _url("/exchange/nonce123"),
        data=json.dumps({"public_token": "public-tok"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as resp:
        assert json.loads(resp.read()) == {"ok": True}
    assert server.done.wait(2)
    assert server.result == {"public_token": "public-tok"}


def test_garbage_body_still_answers_and_signals(server: ExchangeServer) -> None:
    req = urllib.request.Request(_url("/exchange/nonce123"), data=b"not json",
                                 method="POST")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
    assert server.done.wait(2)
    assert server.result == {}
