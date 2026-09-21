import base64
import tempfile
import unittest
from pathlib import Path

import httpx

from nemo.adapters.tools.web import (
    MAX_RESPONSE_BYTES,
    FetchUrlTool,
    WebSearchTool,
    parse_results,
    resolve_result_url,
    strip_html,
)
from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.contracts.types import ToolCall
from nemo.core.tools.approval import ApprovalMode, ApprovalOutcome, ApprovalPolicy
from nemo.core.tools.registry import ToolRegistry

# Shaped like the page Bing actually serves: one <li class="b_algo"> per result,
# the link inside <h2>, the snippet in a b_lineclamp paragraph.
SEARCH_HTML = """
<html><body><ol id="b_results">
<li class="b_algo" data-id="1"><h2 class=""><a target="_blank"
 href="https://docs.python.org/3/library/asyncio.html" h="ID=SERP,1">
 <strong>asyncio</strong> &mdash; Asynchronous I/O</a></h2>
 <div class="b_caption"><p class="b_lineclamp2">asyncio is a library to write
 concurrent code &amp; more.</p></div></li>
<li class="b_algo" data-id="2"><h2><a href="https://example.com/second">Second result</a></h2>
 <p class="b_lineclamp4">Another snippet.</p></li>
</ol></body></html>
"""

PAGE_HTML = """
<html><head><title>T</title><style>body{color:red}</style></head>
<body><script>var secret = "should not appear";</script>
<h1>Heading</h1><p>First &amp; second.</p><noscript>fallback</noscript></body></html>
"""


def transport_returning(body: str, *, status: int = 200, content_type: str = "text/html; charset=utf-8"):
    return httpx.MockTransport(
        lambda request: httpx.Response(status, text=body, headers={"content-type": content_type})
    )


def call(name, arguments, id="c1"):
    return ToolCall(id=id, name=name, arguments=arguments)


class ParsingTests(unittest.TestCase):
    def test_results_are_extracted_without_tags(self):
        results = parse_results(SEARCH_HTML, 5)

        self.assertEqual(len(results), 2)
        title, url, snippet = results[0]
        self.assertEqual(title, "asyncio — Asynchronous I/O")
        self.assertEqual(url, "https://docs.python.org/3/library/asyncio.html")
        self.assertIn("concurrent code & more", snippet)
        self.assertEqual(results[1][0], "Second result")

    def test_limit_is_respected(self):
        self.assertEqual(len(parse_results(SEARCH_HTML, 1)), 1)

    def test_unknown_markup_yields_nothing(self):
        self.assertEqual(parse_results("<html><body>no results here</body></html>", 5), [])

    def test_bing_click_trackers_are_unwrapped(self):
        target = "https://docs.python.org/3/library/asyncio.html"
        encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        wrapped = f"https://www.bing.com/ck/a?!&&p=abc&u=a1{encoded}"

        self.assertEqual(resolve_result_url(wrapped), target)

    def test_ordinary_urls_and_broken_trackers_pass_through(self):
        self.assertEqual(
            resolve_result_url("https://example.com/a"), "https://example.com/a"
        )
        self.assertEqual(
            resolve_result_url("https://www.bing.com/ck/a?u=a1!!!not-base64!!!"),
            "https://www.bing.com/ck/a?u=a1!!!not-base64!!!",
        )

    def test_strip_html_drops_code_and_unwraps_tags(self):
        text = strip_html(PAGE_HTML)

        self.assertIn("Heading", text)
        self.assertIn("First & second.", text)
        self.assertNotIn("should not appear", text)   # <script> content is not text
        self.assertNotIn("color:red", text)           # nor is <style>
        self.assertNotIn("<", text)


class ToolTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.context = ExecutionContext(workspace=Path(self._tmp.name).resolve())

    def registry(self, *tools, policy=None):
        return ToolRegistry(tuple(tools), context=self.context, policy=policy)


class WebSearchTests(ToolTestCase):
    async def test_search_returns_a_numbered_list(self):
        registry = self.registry(WebSearchTool(transport=transport_returning(SEARCH_HTML)))

        result = await registry.execute(call("web_search", {"query": "python asyncio"}))

        self.assertIsNone(result.error)
        self.assertIn("1. asyncio — Asynchronous I/O", result.output)
        self.assertIn("https://docs.python.org/3/library/asyncio.html", result.output)
        self.assertIn("2. Second result", result.output)

    async def test_unparseable_page_is_an_error_not_an_empty_answer(self):
        registry = self.registry(WebSearchTool(transport=transport_returning("<html>nope</html>")))

        result = await registry.execute(call("web_search", {"query": "x"}))

        self.assertEqual(result.error.code, "execution_error")
        self.assertIn("no parseable results", result.error.message)

    async def test_http_error_is_reported(self):
        registry = self.registry(WebSearchTool(transport=transport_returning("nope", status=503)))

        result = await registry.execute(call("web_search", {"query": "x"}))

        self.assertEqual(result.error.code, "execution_error")
        self.assertIn("HTTP 503", result.error.message)

    async def test_timeout_is_reported(self):
        def handler(request):
            raise httpx.ReadTimeout("too slow", request=request)

        registry = self.registry(WebSearchTool(transport=httpx.MockTransport(handler)))

        result = await registry.execute(call("web_search", {"query": "x"}))

        self.assertEqual(result.error.code, "execution_error")
        self.assertIn("timed out", result.error.message)

    async def test_result_count_is_bounded(self):
        registry = self.registry(WebSearchTool(transport=transport_returning(SEARCH_HTML)))

        too_many = await registry.execute(call("web_search", {"query": "x", "max_results": 11}))
        too_few = await registry.execute(call("web_search", {"query": "x", "max_results": 0}))

        self.assertEqual(too_many.error.code, "invalid_arguments")
        self.assertEqual(too_few.error.code, "invalid_arguments")

    async def test_summary_shows_what_left_the_machine(self):
        tool = WebSearchTool()
        self.assertEqual(
            tool.summarize(tool.arguments_type(query="python asyncio")),
            'search "python asyncio"',
        )


class FetchUrlTests(ToolTestCase):
    async def test_page_is_returned_as_text(self):
        registry = self.registry(FetchUrlTool(transport=transport_returning(PAGE_HTML)))

        result = await registry.execute(call("fetch_url", {"url": "https://example.com/a"}))

        self.assertIsNone(result.error)
        self.assertIn("Heading", result.output)
        self.assertNotIn("should not appear", result.output)

    async def test_only_http_and_https_are_allowed(self):
        registry = self.registry(FetchUrlTool(transport=transport_returning(PAGE_HTML)))

        for url in ("file:///etc/passwd", "data:text/plain,hi", "ftp://example.com"):
            with self.subTest(url=url):
                result = await registry.execute(call("fetch_url", {"url": url}))
                self.assertEqual(result.error.code, "execution_error")
                self.assertIn("only http and https", result.error.message)

    async def test_long_pages_are_truncated_with_a_marker(self):
        long_page = "<html><body>" + ("word " * 4000) + "</body></html>"
        registry = self.registry(FetchUrlTool(transport=transport_returning(long_page)))

        result = await registry.execute(call("fetch_url", {"url": "https://example.com"}))

        self.assertIn("[truncated: showing the first", result.output)
        self.assertLessEqual(len(result.output), self.context.max_output_chars + 100)

    async def test_oversized_bodies_are_cut_at_the_byte_cap(self):
        huge = "x" * (MAX_RESPONSE_BYTES + 5000)
        registry = self.registry(
            FetchUrlTool(transport=transport_returning(huge, content_type="text/plain"))
        )

        result = await registry.execute(call("fetch_url", {"url": "https://example.com"}))

        self.assertIsNone(result.error)
        self.assertLessEqual(len(result.output), self.context.max_output_chars + 100)


class ReadOnlyClassificationTests(ToolTestCase):
    async def test_web_tools_never_ask_for_approval(self):
        asked = []

        class Approver:
            async def __call__(self, request):
                asked.append(request)
                return ApprovalOutcome.DENY

        policy = ApprovalPolicy(ApprovalMode.ASK, approver=Approver())
        registry = self.registry(
            WebSearchTool(transport=transport_returning(SEARCH_HTML)),
            FetchUrlTool(transport=transport_returning(PAGE_HTML)),
            policy=policy,
        )

        searched = await registry.execute(call("web_search", {"query": "x"}, id="c1"))
        fetched = await registry.execute(call("fetch_url", {"url": "https://e.com"}, id="c2"))

        self.assertIsNone(searched.error)
        self.assertIsNone(fetched.error)
        self.assertEqual(asked, [])
