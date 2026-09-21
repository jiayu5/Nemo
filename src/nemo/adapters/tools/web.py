"""Web tools: search the open web and read a page.

The only place in Nemo that reaches the internet on the model's behalf. Two
tools, because they answer two different questions: ``web_search`` finds
candidates, ``fetch_url`` reads one.

Both are ``read_only``: approval exists to confirm *changes*, and an HTTP GET
changes nothing on this machine. What replaces that confirmation is visibility --
every query and every URL is written into the ``tool.started`` summary, so the
terminal and the transcript both show what left the machine.

The HTML parsing here is deliberately small and regex-based: the goal is text a
model can read, not a faithful rendering. See ``docs/design/web-tools.md`` for
why the search backend is a scrape rather than an API, and what that costs.
"""

from __future__ import annotations

import base64
import binascii
import html as html_module
import re
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.tools.limits import truncate_text

SEARCH_ENDPOINT = "https://www.bing.com/search"

#: Hard ceiling on how much of a response body is read into memory. A page that
#: streams forever must not be able to end the process.
MAX_RESPONSE_BYTES = 2_000_000

#: Search engines serve different markup to obvious bots; this identifies as an
#: ordinary browser so the results page is the one a person would see.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_RESULT_BLOCK = re.compile(r'<li class="b_algo".*?(?=<li class="b_algo"|</ol>)', re.S)
_RESULT_LINK = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_RESULT_SNIPPET = re.compile(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', re.S)
_SCRIPT_OR_STYLE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_BLANK_RUN = re.compile(r"\n{3,}")


def resolve_result_url(url: str) -> str:
    """Unwrap a search engine click tracker into the real destination.

    Bing returns ``https://www.bing.com/ck/a?...&u=a1<base64url>`` rather than the
    target. Handing that to ``fetch_url`` returns a "please click here" stub, so
    the wrapper is decoded here and never reaches the model.
    """

    parsed = urlparse(url)
    if not parsed.netloc.endswith("bing.com") or "/ck/a" not in parsed.path:
        return url
    raw = parse_qs(parsed.query).get("u", [""])[0]
    if raw.startswith("a1"):
        raw = raw[2:]
    if not raw:
        return url
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode(
            "utf-8", "replace"
        )
    except (binascii.Error, ValueError):
        return url
    return decoded if decoded.startswith(("http://", "https://")) else url


def strip_html(markup: str) -> str:
    """Turn a page into readable text: drop code, unwrap tags, decode entities."""

    text = _SCRIPT_OR_STYLE.sub(" ", markup)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text)
    text = _TAG.sub("", text)
    text = html_module.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return _BLANK_RUN.sub("\n\n", "\n".join(line for line in lines if line)).strip()


def parse_results(markup: str, limit: int) -> list[tuple[str, str, str]]:
    """Extract ``(title, url, snippet)`` triples from a results page.

    Returns whatever it could parse. An empty list means the page no longer has
    the shape this parser knows, which the caller reports as a failure rather
    than as "no results" -- those are different facts and only one of them is
    actionable.
    """

    results: list[tuple[str, str, str]] = []
    for block in _RESULT_BLOCK.findall(markup):
        link = _RESULT_LINK.search(block)
        if link is None:
            continue
        url = resolve_result_url(html_module.unescape(link.group(1)).strip())
        title = strip_html(link.group(2))
        snippet_match = _RESULT_SNIPPET.search(block)
        snippet = strip_html(snippet_match.group(1)) if snippet_match else ""
        if not url.startswith(("http://", "https://")) or not title:
            continue
        results.append((title, url, snippet))
        if len(results) >= limit:
            break
    return results


class WebSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500, description="What to search for")
    max_results: int = Field(
        default=5, ge=1, le=10, description="How many results to return, 1 to 10"
    )


class WebSearchTool:
    name = "web_search"
    description = (
        "Search the web and return a numbered list of results with titles, URLs and "
        "short snippets. Use fetch_url afterwards to read a result in full. Results "
        "are as reported by the search engine; this tool does not verify them."
    )
    arguments_type = WebSearchArguments
    read_only = True

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    async def execute(self, arguments: WebSearchArguments, context: ExecutionContext) -> str:
        url = f"{SEARCH_ENDPOINT}?q={quote_plus(arguments.query)}"
        response, body = await _get(url, context=context, transport=self._transport)
        encoding = response.charset_encoding or "utf-8"
        markup = body.decode(encoding, errors="replace")
        results = parse_results(markup, arguments.max_results)
        if not results:
            raise ToolFailure(
                "the search page had no parseable results; it may have changed shape "
                "or refused the request"
            )
        rendered = "\n\n".join(
            f"{index}. {title}\n   {url}" + (f"\n   {snippet}" if snippet else "")
            for index, (title, url, snippet) in enumerate(results, start=1)
        )
        return truncate_text(rendered, context.max_output_chars)

    def summarize(self, arguments: WebSearchArguments) -> str:
        return f'search "{arguments.query}"'


class FetchUrlArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2000, description="Absolute http or https URL")
    max_chars: int | None = Field(
        default=None, gt=0, description="Override the default output limit"
    )


class FetchUrlTool:
    name = "fetch_url"
    description = (
        "Fetch one http or https URL and return its readable text with HTML tags "
        "removed. Use it to read a page found by web_search or given by the user."
    )
    arguments_type = FetchUrlArguments
    read_only = True

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    async def execute(self, arguments: FetchUrlArguments, context: ExecutionContext) -> str:
        parsed = urlparse(arguments.url)
        if parsed.scheme not in ("http", "https"):
            # Anything else (file://, data:) would be a way around the workspace
            # boundary dressed up as a network call.
            raise ToolFailure("only http and https URLs can be fetched")
        if not parsed.netloc:
            raise ToolFailure("the URL has no host")

        response, body = await _get(
            arguments.url, context=context, transport=self._transport
        )
        encoding = response.charset_encoding or "utf-8"
        text = body.decode(encoding, errors="replace")
        content_type = response.headers.get("content-type", "")
        if "html" in content_type.lower() or "<html" in text[:2000].lower():
            text = strip_html(text)
        limit = arguments.max_chars or context.max_output_chars
        return truncate_text(text.strip(), limit)

    def summarize(self, arguments: FetchUrlArguments) -> str:
        return f"fetch {arguments.url}"


async def _get(
    url: str,
    *,
    context: ExecutionContext,
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[httpx.Response, bytes]:
    """One bounded GET. Returns the response and at most ``MAX_RESPONSE_BYTES``."""

    timeout = context.default_timeout_seconds
    try:
        async with httpx.AsyncClient(
            transport=transport,
            follow_redirects=True,
            max_redirects=5,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en,zh;q=0.9"},
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise ToolFailure(f"the server answered HTTP {response.status_code}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= MAX_RESPONSE_BYTES:
                        break
                return response, bytes(body)
    except ToolFailure:
        raise
    except httpx.TimeoutException:
        raise ToolFailure(f"the request timed out after {timeout:g}s") from None
    except httpx.HTTPError as exc:
        # The exception text can carry the full URL; keep the message generic but
        # say which kind of failure it was, because that is what the model needs.
        raise ToolFailure(f"the request failed: {type(exc).__name__}") from None
