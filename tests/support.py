"""Shared test scaffolding.

The WSGI client here calls the application object directly. There is no socket
and no server thread, which makes the API tests as fast and as deterministic as
the unit tests, and it still exercises the real request path: environ in,
status/headers/body out.
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "fixtures", "corpus")


class Response(object):
    def __init__(self, status, headers, body):
        self.status = status
        self.status_code = int(status.split(" ", 1)[0])
        self.headers = {k.lower(): v for k, v in headers}
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def etag(self):
        return self.headers.get("etag")


class Client(object):
    def __init__(self, app):
        self.app = app

    def request(self, path, method="GET", headers=None):
        if "?" in path:
            path, _, query = path.partition("?")
        else:
            query = ""
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "SERVER_NAME": "testserver",
            "SERVER_PORT": "80",
            "wsgi.url_scheme": "http",
        }
        for key, value in (headers or {}).items():
            environ["HTTP_" + key.upper().replace("-", "_")] = value

        captured = {}

        def start_response(status, response_headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = response_headers

        chunks = self.app(environ, start_response)
        body = b"".join(chunks)
        return Response(captured["status"], captured["headers"], body)

    def get(self, path, headers=None):
        return self.request(path, "GET", headers)


def build_app(db_path=":memory:"):
    from server import api
    return api.build(db_path=db_path, corpus_dir=CORPUS)


def fresh_store():
    from server import store as store_mod
    return store_mod.sqlite_store(":memory:")
