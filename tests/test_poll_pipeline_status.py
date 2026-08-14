"""Tests for the dependency-free pipeline status client."""

import importlib.util
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock
from urllib.error import URLError


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "autoops" / "autoops-cicd" / "hooks" / "poll_pipeline_status.py"
SPEC = importlib.util.spec_from_file_location("poll_pipeline_status", str(SCRIPT))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeHeaders:
    def get_content_charset(self):
        return "utf-8"


class FakeResponse:
    headers = FakeHeaders()

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self):
        return self.body


class PipelineStatusTest(unittest.TestCase):
    def test_get_build_status_encodes_query_and_reads_response(self):
        response = FakeResponse("构建成功".encode("utf-8"))
        with mock.patch.object(MODULE, "urlopen", return_value=response) as urlopen:
            status = MODULE.get_build_status("pipeline A", "1&2")

        self.assertEqual(status, "构建成功")
        self.assertIn("pipelineCode=pipeline+A", urlopen.call_args[0][0])
        self.assertIn("pipelineNum=1%262", urlopen.call_args[0][0])
        self.assertEqual(urlopen.call_args[1]["timeout"], 10)

    def test_get_build_status_handles_empty_response(self):
        with mock.patch.object(MODULE, "urlopen", return_value=FakeResponse(b"  ")):
            self.assertEqual(MODULE.get_build_status("p", "1"), "获取状态失败: 空响应")

    def test_get_build_status_reports_network_error(self):
        stderr = io.StringIO()
        with mock.patch.object(MODULE, "urlopen", side_effect=URLError("offline")):
            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                MODULE.get_build_status("p", "1")

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("请求接口异常", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
