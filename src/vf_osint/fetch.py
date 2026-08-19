from __future__ import annotations

import io
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from .models import PublicDocument, SourceClass
from .policy import CollectionPolicy


class CollectionRejected(RuntimeError):
    pass


@dataclass
class CrawlResult:
    documents: list[PublicDocument]
    rejected: list[dict[str, str]]


class PublicWebFetcher:
    def __init__(self, policy: CollectionPolicy | None = None):
        self.policy = policy or CollectionPolicy()
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}
        self._lock = threading.Lock()
        self.client = httpx.Client(
            headers={"User-Agent": self.policy.user_agent},
            timeout=self.policy.timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def _respect_interval(self, host: str) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request.get(host, 0.0)
            remaining = self.policy.per_host_interval_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
            self._last_request[host] = time.monotonic()

    def _robots_allowed(self, url: str) -> bool:
        if not self.policy.obey_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        robot = self._robots.get(origin)
        if robot is None:
            robot = RobotFileParser(urljoin(origin, "/robots.txt"))
            try:
                response = self.client.get(robot.url)
                if response.status_code < 400:
                    robot.parse(response.text.splitlines())
                else:
                    robot.parse([])
            except httpx.HTTPError:
                robot.parse([])
            self._robots[origin] = robot
        return robot.can_fetch(self.policy.user_agent, url)

    def fetch(self, url: str, source_class: SourceClass) -> PublicDocument:
        allowed, reason = self.policy.allows_url(url)
        if not allowed:
            raise CollectionRejected(reason)
        if not self._robots_allowed(url):
            raise CollectionRejected("robots_disallowed")
        host = urlparse(url).hostname or ""
        self._respect_interval(host)
        with self.client.stream("GET", url) as response:
            response.raise_for_status()
            total = 0
            chunks = []
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > self.policy.max_response_bytes:
                    raise CollectionRejected("response_too_large")
                chunks.append(chunk)
            body = b"".join(chunks)
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            final_url = str(response.url)

        if content_type in {"text/html", "application/xhtml+xml", "text/plain", ""}:
            return self._html_document(final_url, body, response.encoding, response.status_code, source_class, content_type)
        if content_type == "application/pdf" or final_url.lower().endswith(".pdf"):
            return self._pdf_document(final_url, body, response.status_code, source_class)
        raise CollectionRejected(f"unsupported_content_type:{content_type}")

    @staticmethod
    def _html_document(
        url: str,
        body: bytes,
        encoding: str | None,
        status: int,
        source_class: SourceClass,
        content_type: str,
    ) -> PublicDocument:
        decoded = body.decode(encoding or "utf-8", errors="replace")
        soup = BeautifulSoup(decoded, "lxml")
        for element in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
            element.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else url
        text = "\n".join(
            line.strip() for line in soup.get_text("\n").splitlines() if len(line.strip()) >= 2
        )
        links = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "").strip()
            if href.casefold().startswith("mailto:"):
                text += "\n" + href.split(":", 1)[1].split("?", 1)[0]
                continue
            if href.casefold().startswith("tel:"):
                text += "\n" + href.split(":", 1)[1]
                continue
            absolute, _ = urldefrag(urljoin(url, href))
            if absolute.startswith(("http://", "https://")):
                links.append(absolute)
        published = _extract_published_date(soup)
        return PublicDocument(
            url=url,
            title=title,
            text=text,
            source_class=source_class,
            published_at=published,
            http_status=status,
            content_type=content_type or "text/html",
            links=list(dict.fromkeys(links)),
        )

    @staticmethod
    def _pdf_document(url: str, body: bytes, status: int, source_class: SourceClass) -> PublicDocument:
        reader = PdfReader(io.BytesIO(body))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        title = str(reader.metadata.title) if reader.metadata and reader.metadata.title else url.rsplit("/", 1)[-1]
        return PublicDocument(
            url=url,
            title=title,
            text=text,
            source_class=source_class,
            http_status=status,
            content_type="application/pdf",
        )


class SmartCrawler:
    PRIORITY_TERMS = {
        "processo": 6,
        "execucao": 6,
        "penhora": 6,
        "sisbajud": 6,
        "juridico": 5,
        "governanca": 4,
        "governança": 4,
        "diretoria": 6,
        "lideranca": 5,
        "liderança": 5,
        "equipe": 5,
        "time": 4,
        "quem-somos": 5,
        "quem_somos": 5,
        "empresa": 3,
        "institucional": 3,
        "sobre": 4,
        "contato": 6,
    }

    def __init__(self, fetcher: PublicWebFetcher):
        self.fetcher = fetcher

    def crawl(self, seeds: list[tuple[str, SourceClass]], query_terms: list[str]) -> CrawlResult:
        queue: deque[tuple[int, int, str, SourceClass, str]] = deque()
        for url, source_class in seeds:
            queue.append((100, 0, url, source_class, urlparse(url).netloc.lower()))
        visited: set[str] = set()
        documents: list[PublicDocument] = []
        rejected: list[dict[str, str]] = []
        query = {term.casefold() for term in query_terms if term}
        while queue and len(documents) < self.fetcher.policy.max_pages_per_run:
            ordered = sorted(queue, key=lambda item: (-item[0], item[1], item[2]))
            queue = deque(ordered)
            _, depth, url, source_class, seed_host = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            try:
                document = self.fetcher.fetch(url, source_class)
            except (CollectionRejected, httpx.HTTPError, ValueError) as exc:
                rejected.append({"url": url, "reason": str(exc)[:300]})
                continue
            documents.append(document)
            if depth >= self.fetcher.policy.max_depth:
                continue
            for link in document.links:
                if urlparse(link).netloc.lower() != seed_host or link in visited:
                    continue
                lowered = link.casefold()
                score = sum(weight for term, weight in self.PRIORITY_TERMS.items() if term in lowered)
                score += sum(4 for term in query if term in lowered)
                if score >= 4:
                    queue.append((score, depth + 1, link, source_class, seed_host))
        return CrawlResult(documents=documents, rejected=rejected)


def _extract_published_date(soup: BeautifulSoup) -> datetime | None:
    candidates = [
        soup.select_one('meta[property="article:published_time"]'),
        soup.select_one('meta[name="date"]'),
        soup.select_one("time[datetime]"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        raw = candidate.get("content") or candidate.get("datetime") or ""
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None
