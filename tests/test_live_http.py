
"""Live-mode HTTP path, tested against a local OpenAI-compatible server on 127.0.0.1.

No internet access is used. The server is a real HTTP server, so the request, the status codes,
the error handling and the retries all run through the real urllib code path.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from config.settings import Settings
from orchestration.engine import Orchestrator
from services.llm_client import LLMError, OpenAIChatClient, build_llm_client
from tests.helpers import good_report

KEY = "sk-LOCAL-TEST-KEY-12345"

TASK = (
    "Compare artificial intelligence and machine learning trends in healthcare, "
    "analyse real-world applications and challenges, and write an executive brief."
)

PLAN = json.dumps({
    "steps": [
        {
            "id": "S1",
            "agent": "research",
            "title": "Research artificial intelligence",
            "instruction": "Find relevant AI and machine learning statements",
            "depends_on": [],
            "focus": "artificial",
        },
        {
            "id": "S2",
            "agent": "analysis",
            "title": "Analyse AI trends",
            "instruction": "Analyse the retrieved evidence",
            "depends_on": ["S1"],
            "focus": "",
        },
        {
            "id": "S3",
            "agent": "writing",
            "title": "Write executive brief",
            "instruction": "Draft an evidence-based report",
            "depends_on": ["S2", "S1"],
            "focus": "",
        },
        {
            "id": "S4",
            "agent": "verification",
            "title": "Verify report",
            "instruction": "Check the report claims against sources",
            "depends_on": ["S3"],
            "focus": "",
        },
    ],
    "assumptions": ["Plan returned by the local test server."],
})


class _Handler(BaseHTTPRequestHandler):
    responses: list = []
    seen: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        _Handler.seen.append((self.path, dict(self.headers), body))
        item = _Handler.responses.pop(0)
        status, payload = item(body) if callable(item) else item
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        return


@pytest.fixture
def local_server():
    _Handler.responses = []
    _Handler.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, _Handler
    server.shutdown()
    server.server_close()


def base_url(server):
    return f"http://127.0.0.1:{server.server_address[1]}/v1"


def chat(content):
    return {"choices": [{"message": {"content": content}}]}


def writer_response(body):
    messages = json.loads(body)["messages"]
    report = good_report(messages[0]["content"], messages[1]["content"])
    return 200, chat(report)


def test_openai_client_talks_to_a_real_http_endpoint(local_server):
    server, handler = local_server
    handler.responses = [(200, chat("live reply"))]
    client = OpenAIChatClient(KEY, "test-model", base_url(server), 5.0)

    assert client.complete("ROLE=analysis\nrules", "hello") == "live reply"

    path, headers, body = handler.seen[0]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert json.loads(body)["model"] == "test-model"


def test_provider_errors_are_redacted_and_classified(local_server):
    server, handler = local_server
    handler.responses = [
        (401, {"error": {"message": f"invalid key {KEY}"}}),
        (503, {"error": "overloaded"}),
    ]
    client = OpenAIChatClient(KEY, "test-model", base_url(server), 5.0)

    try:
        client.complete("ROLE=x\nrules", "hi")
        raised = None
    except LLMError as exc:
        raised = exc

    assert raised is not None and raised.retryable is False
    assert KEY not in str(raised)
    assert "[REDACTED]" in str(raised)

    try:
        client.complete("ROLE=x\nrules", "hi")
        raised = None
    except LLMError as exc:
        raised = exc

    assert raised is not None and raised.retryable is True


def test_a_provider_503_is_retried_and_the_run_still_completes(
    local_server, db, store, tmp_path
):
    server, handler = local_server
    handler.responses = [
        (503, {"error": "temporarily overloaded"}),
        (200, chat(PLAN)),
        (200, chat("- The retrieved statements describe AI applications [S1]")),
        writer_response,
    ]

    settings = Settings(
        provider="openai",
        openai_api_key=KEY,
        openai_base_url=base_url(server),
        llm_timeout_seconds=5.0,
        retry_backoff_seconds=0.0,
        db_path=tmp_path / "live.db",
    )

    llm = build_llm_client(settings)
    orchestrator = Orchestrator(
        db, store, settings, llm=llm, sleep=lambda seconds: None
    )
    run = orchestrator.create_run(TASK)
    orchestrator.run_to_completion(run)

    assert run.plan.planner == "llm:openai"
    assert len(handler.seen) == 4
    assert run.status in ("completed", "completed_with_warnings")
    assert run.context.verification["unsupported"] == 0

    stored = (
        json.dumps(db.get_task(run.task_id), default=str)
        + json.dumps(db.get_logs(run.task_id), default=str)
    )
    assert KEY not in stored
