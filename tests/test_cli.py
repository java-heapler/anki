import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from anki_agent import cli


@pytest.fixture
def server():
    state = {"requests": [], "result": None, "error": None, "status": 200}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(state["status"])
            self.end_headers()
            body = state.get("raw", json.dumps({"result": state["result"], "error": state["error"]}).encode())
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{http.server_port}"
    yield state
    http.shutdown()
    thread.join()
    http.server_close()


def invoke(server, *args):
    return cli.execute(cli.parser().parse_args(["--endpoint", server["url"], *args]))


@pytest.mark.parametrize("url", ["https://127.0.0.1", "http://example.com", "http://127.0.0.1/a", "http://user:secret@localhost", "http://localhost/?key=secret", "http://localhost/#x", "http://[bad", "http://localhost:bad"])
def test_bad_endpoint(url):
    with pytest.raises(cli.AnkiError, match="loopback"):
        cli.Client(url)


@pytest.mark.parametrize("timeout", [0, -1, 121, float("nan"), float("inf")])
def test_timeout_validation(timeout):
    with pytest.raises(cli.AnkiError, match="Timeout"):
        cli.Client(timeout=timeout)


def test_api_contract(server):
    server["result"] = ["Default", "日本語"]
    assert cli.Client(server["url"], key="private").call("deckNames") == server["result"]
    assert server["requests"] == [{"action": "deckNames", "version": 6, "params": {}, "key": "private"}]
    assert cli.Client("http://[::1]:8765").host == "::1"


@pytest.mark.parametrize("state,match", [({"status": 302}, "HTTP 302"), ({"status": 500}, "HTTP 500"), ({"raw": b"not-json"}, "invalid JSON"), ({"raw": b"\xff"}, "invalid JSON"), ({"raw": b"[]"}, "envelope"), ({"raw": b'{}'}, "envelope"), ({"error": "secret"}, "rejected"), ({"raw": b"x" * (8*1024*1024+1)}, "8 MiB")])
def test_response_errors(server, state, match):
    server.update(state)
    with pytest.raises(cli.AnkiError, match=match) as exc:
        cli.Client(server["url"]).call("version")
    assert "secret" not in str(exc.value)
    assert len(server["requests"]) == 1


def test_connection_failure(monkeypatch):
    def fail(*args):
        raise TimeoutError("secret")
    monkeypatch.setattr(cli.http.client.HTTPConnection, "request", fail)
    with pytest.raises(cli.AnkiError, match="outcome may be unknown"):
        cli.Client().call("addNote")


@pytest.mark.parametrize("command,result,action,params", [
    (["doctor"], 6, "version", {}), (["decks"], ["Default"], "deckNames", {}),
    (["models"], ["Basic"], "modelNames", {}), (["model-fields", "Basic"], ["Front", "Back"], "modelFieldNames", {"modelName": "Basic"}),
    (["notes", "1", "2"], [{"noteId": 1}], "notesInfo", {"notes": [1, 2]}),
    (["cards", "3"], [{"cardId": 3}], "cardsInfo", {"cards": [3]}),
])
def test_read_commands(server, command, result, action, params):
    server["result"] = result
    output = invoke(server, *command)
    assert output == ({"connected": True, "api_version": 6} if command == ["doctor"] else result)
    assert server["requests"][0] == {"action": action, "version": 6, "params": params}


@pytest.mark.parametrize("version", [5, "6"])
def test_old_api(server, version):
    server["result"] = version
    with pytest.raises(cli.AnkiError, match="version 6"):
        invoke(server, "doctor")


@pytest.mark.parametrize("command,action", [("find-notes", "findNotes"), ("find-cards", "findCards")])
def test_search(server, command, action):
    server["result"] = [3, 1, 2]
    assert invoke(server, command, "tag:python", "--limit", "1", "--offset", "1") == {"ids": [2], "total": 3, "next_offset": 2}
    assert invoke(server, command, "tag:python", "--offset", "2") == {"ids": [3], "total": 3, "next_offset": None}
    assert server["requests"][0]["action"] == action
    with pytest.raises(cli.AnkiError, match="Offset"):
        invoke(server, command, "x", "--offset", "-1")
    for bad in (None, [True], ["1"]):
        server["result"] = bad
        with pytest.raises(cli.AnkiError, match="ID list"):
            invoke(server, command, "x")


@pytest.mark.parametrize("content", ["invalid", "[]", "{}", '{"": "x"}', '{"Front": 3}'])
def test_fields_invalid(tmp_path, content):
    p = tmp_path / "fields.json"
    p.write_text(content)
    with pytest.raises(cli.AnkiError):
        cli.fields_file(p)
    with pytest.raises(cli.AnkiError):
        cli.fields_file(tmp_path / "missing")


@pytest.fixture
def fields(tmp_path):
    path = tmp_path / "fields.json"
    path.write_text(json.dumps({"Front": "What is O(n)?", "Back": "Linear time"}))
    return str(path)


def test_add_preview_and_write(server, fields):
    args = ["add-note", "--deck", "Default", "--model", "Basic", "--fields-file", fields, "--tag", "swe"]
    preview = invoke(server, *args)
    assert preview["applied"] is False and preview["note"]["options"] == {"allowDuplicate": False}
    assert server["requests"] == []
    server["result"] = 123
    assert invoke(server, *args, "--confirm") == {"action": "addNote", "result": 123, "applied": True}
    assert server["requests"][0]["params"]["note"] == preview["note"]


def test_update_verified(server, fields, monkeypatch):
    args = ["update-note", "123", "--fields-file", fields]
    assert invoke(server, *args)["note"]["id"] == 123
    assert server["requests"] == []
    calls = []
    def call(self, action, **params):
        calls.append((action, params))
        if action == "notesInfo":
            return [{"fields": {k: {"value": v} for k, v in cli.fields_file(fields).items()}}]
        return None
    monkeypatch.setattr(cli.Client, "call", call)
    assert invoke(server, *args, "--confirm")["applied"] is True
    assert calls[0][0] == "updateNoteFields" and calls[1] == ("notesInfo", {"notes": [123]})


@pytest.mark.parametrize("observed", [None, [], [None], [{"fields": None}], [{"fields": {}}], [{"fields": {"Front": None}}], [{"fields": {"Front": {"value": "wrong"}}}]])
def test_update_mismatch(server, fields, monkeypatch, observed):
    monkeypatch.setattr(cli.Client, "call", lambda *args, **kwargs: observed)
    with pytest.raises(cli.AnkiError, match="read-back"):
        invoke(server, "update-note", "123", "--fields-file", fields, "--confirm")


def test_cli_json_and_exit(server, capsys, monkeypatch):
    monkeypatch.setenv("ANKI_CONNECT_URL", server["url"])
    server["result"] = 6
    assert cli.main(["doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["connected"] is True
    server["error"] = "private"
    assert cli.main(["doctor"]) == 1
    assert "private" not in capsys.readouterr().out
    monkeypatch.setattr(cli, "main", lambda: 0)
    with pytest.raises(SystemExit) as exc:
        cli.run()
    assert exc.value.code == 0


@pytest.mark.parametrize("args", [["notes", "0"], ["find-notes", "x", "--limit", "-1"], ["cards", "no"]])
def test_arg_errors(args):
    with pytest.raises(SystemExit) as exc:
        cli.parser().parse_args(args)
    assert exc.value.code == 2


def test_installed_cli_subprocess(server, fields):
    env = dict(os.environ, ANKI_CONNECT_URL=server["url"])
    server["result"] = 6
    proc = subprocess.run(["anki-agent", "doctor"], env=env, text=True, capture_output=True)
    assert proc.returncode == 0 and json.loads(proc.stdout)["connected"]
    proc = subprocess.run(["anki-agent", "add-note", "--deck", "Default", "--model", "Basic", "--fields-file", fields], env=env, text=True, capture_output=True)
    assert proc.returncode == 0 and json.loads(proc.stdout)["preview"]
    assert len(server["requests"]) == 1


@pytest.mark.parametrize("result", [None, True, 0, "123"])
def test_add_invalid_result(server, fields, result):
    server["result"] = result
    with pytest.raises(cli.AnkiError, match="inspect Anki"):
        invoke(server, "add-note", "--deck", "Default", "--model", "Basic", "--fields-file", fields, "--confirm")


def test_write_error_unknown(server, fields):
    server["status"] = 500
    with pytest.raises(cli.AnkiError, match="Write outcome may be unknown"):
        invoke(server, "update-note", "123", "--fields-file", fields, "--confirm")
    assert len(server["requests"]) == 1


def test_update_verification_failure(server, fields, monkeypatch):
    def call(self, action, **params):
        if action == "notesInfo":
            raise cli.AnkiError("failed")
        return None
    monkeypatch.setattr(cli.Client, "call", call)
    with pytest.raises(cli.AnkiError, match="verification failed"):
        invoke(server, "update-note", "123", "--fields-file", fields, "--confirm")
