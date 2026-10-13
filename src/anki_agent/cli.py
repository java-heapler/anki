"""Small, explicit AnkiConnect API v6 adapter; no collection database access."""
import argparse
import http.client
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit


class AnkiError(Exception):
    """An actionable error safe to print without credentials or note contents."""


class Client:
    def __init__(self, endpoint="http://127.0.0.1:8765", timeout=10, key=None):
        try:
            url = urlsplit(endpoint)
            port = url.port or 8765
            valid = (url.scheme == "http" and url.hostname in ("127.0.0.1", "::1", "localhost")
                     and not url.username and not url.password and not url.query
                     and not url.fragment and url.path in ("", "/"))
        except ValueError:
            valid = False
        if not valid:
            raise AnkiError("Endpoint must be a loopback HTTP URL without credentials, path, or query.")
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise AnkiError("Timeout must be greater than 0 and at most 120 seconds.")
        self.host, self.port, self.timeout, self.key = url.hostname, port, timeout, key

    def call(self, action, **params):
        payload = {"action": action, "version": 6, "params": params}
        if self.key:
            payload["key"] = self.key
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            connection.request("POST", "/", json.dumps(payload).encode(), {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise AnkiError(f"AnkiConnect returned HTTP {response.status}; redirects are not followed.")
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise AnkiError("AnkiConnect response exceeds 8 MiB; narrow the query.")
        except (OSError, http.client.HTTPException):
            raise AnkiError("Cannot reach AnkiConnect. Open Anki with the add-on enabled; check the port and API key. For writes, inspect the collection before retrying: the outcome may be unknown.") from None
        finally:
            connection.close()
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise AnkiError("AnkiConnect returned invalid JSON.") from None
        if not isinstance(result, dict) or set(result) != {"result", "error"}:
            raise AnkiError("AnkiConnect returned an invalid API v6 envelope.")
        if result["error"] is not None:
            # Server errors can echo fields or secrets. Keep them out of CLI output.
            raise AnkiError(f"AnkiConnect rejected {action}. Check permissions, deck/model/field names, and duplicates in Anki.")
        return result["result"]


def fields_file(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise AnkiError("Cannot read fields file; provide a UTF-8 JSON object mapping field names to strings.") from None
    if not isinstance(value, dict) or not value or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) for k, v in value.items()):
        raise AnkiError("Fields must be a nonempty JSON object mapping nonempty field names to strings.")
    return value


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be a positive integer.")
    return number


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--endpoint", default=os.environ.get("ANKI_CONNECT_URL", "http://127.0.0.1:8765"))
    p.add_argument("--timeout", type=float, default=10)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("doctor", "decks", "models"):
        sub.add_parser(name)
    model = sub.add_parser("model-fields")
    model.add_argument("model")
    for name in ("find-notes", "find-cards"):
        find = sub.add_parser(name)
        find.add_argument("query", help="Anki browser query, e.g. deck:Default")
        find.add_argument("--limit", type=positive, default=50)
        find.add_argument("--offset", type=int, default=0)
    for name in ("notes", "cards"):
        read = sub.add_parser(name)
        read.add_argument("ids", type=positive, nargs="+")
    for name in ("add-note", "update-note"):
        write = sub.add_parser(name)
        write.add_argument("--fields-file", required=True)
        write.add_argument("--confirm", action="store_true", help="Apply the requested write; otherwise preview only.")
        if name == "add-note":
            write.add_argument("--deck", required=True)
            write.add_argument("--model", required=True)
            write.add_argument("--tag", action="append", default=[])
        else:
            write.add_argument("id", type=positive)
    return p


def execute(args):
    client = Client(args.endpoint, args.timeout, os.environ.get("ANKI_CONNECT_KEY"))
    command = args.command
    if command in ("add-note", "update-note"):
        note = {"fields": fields_file(args.fields_file)}
        if command == "add-note":
            note.update(deckName=args.deck, modelName=args.model, tags=args.tag,
                        options={"allowDuplicate": False})
            action = "addNote"
        else:
            note["id"] = args.id
            action = "updateNoteFields"
        if not args.confirm:
            return {"preview": True, "action": action, "note": note, "applied": False}
        try:
            result = client.call(action, note=note)
        except AnkiError as exc:
            raise AnkiError(f"{exc} Write outcome may be unknown; inspect Anki before retrying.") from None
        if command == "add-note" and (type(result) is not int or result <= 0):
            raise AnkiError("Add was submitted but no valid note ID was returned; inspect Anki before retrying.")
        if command == "update-note":
            try:
                observed = client.call("notesInfo", notes=[args.id])
            except AnkiError:
                raise AnkiError("Update was submitted but verification failed; inspect Anki before retrying.") from None
            if (not isinstance(observed, list) or len(observed) != 1
                    or not isinstance(observed[0], dict)
                    or not isinstance(observed[0].get("fields"), dict)
                    or any(not isinstance(observed[0]["fields"].get(k), dict)
                           or observed[0]["fields"][k].get("value") != v for k, v in note["fields"].items())):
                raise AnkiError("Update was submitted but read-back does not match. Close the note editor and inspect Anki before retrying.")
        return {"action": action, "result": result, "applied": True}
    if command == "doctor":
        version = client.call("version")
        if not isinstance(version, int) or version < 6:
            raise AnkiError("AnkiConnect API version 6 or newer is required.")
        return {"connected": True, "api_version": version}
    if command in ("decks", "models"):
        return client.call({"decks": "deckNames", "models": "modelNames"}[command])
    if command == "model-fields":
        return client.call("modelFieldNames", modelName=args.model)
    if command.startswith("find-"):
        if args.offset < 0:
            raise AnkiError("Offset must be zero or greater.")
        result = client.call("findNotes" if command == "find-notes" else "findCards", query=args.query)
        if not isinstance(result, list) or any(type(i) is not int for i in result):
            raise AnkiError("AnkiConnect returned an invalid ID list.")
        result.sort()
        end = args.offset + args.limit
        return {"ids": result[args.offset:end], "total": len(result), "next_offset": end if end < len(result) else None}
    return client.call("notesInfo" if command == "notes" else "cardsInfo", **{command: args.ids})


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except AnkiError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


def run():
    raise SystemExit(main())
