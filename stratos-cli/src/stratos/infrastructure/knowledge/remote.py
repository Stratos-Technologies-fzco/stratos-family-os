"""Remote knowledge sources: GitHub repositories, Confluence and SharePoint (Microsoft Graph).

All use the async API client (TLS only, retries, rate limits). Documents are fetched at query
time; nothing is cached. Only the shapes below were verified against mocked responses; a live
tenant needs the credentials named on each class.
"""

import asyncio
import base64
import contextlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from stratos.domain.exceptions import (
    AuthenticationError,
    ConfigurationError,
    ResourceNotFoundError,
    ValidationError,
)
from stratos.domain.models.extensions import KnowledgeDocument, KnowledgeHit, KnowledgeStatus
from stratos.infrastructure.api.async_client import AsyncPlatformApiClient
from stratos.infrastructure.knowledge.scoring import markdown_title, rank
from stratos.utils.html_text import html_to_text
from stratos.utils.redaction import SecretRedactor
from stratos.utils.validation import require_secure_url, validate_name

MAX_DOC_BYTES = 1024 * 1024
MAX_REMOTE_DOCS = 100
_SPACE = re.compile(r"^[A-Za-z0-9~_-]{1,50}$")
_SITE = re.compile(r"^[A-Za-z0-9.\-]+(:/[A-Za-z0-9_\-/]+)?$")
_TEXT_TYPES = (".md", ".txt", ".html", ".htm")


# ---- GitHub repositories ----------------------------------------------------------------
class GitHubKnowledgeProvider:
    """Markdown documents in GitHub repositories. Source format: `org/repo` or `org/repo:path`.
    Credentials: GITHUB_TOKEN / GH_TOKEN (or the GitHub CLI login)."""

    name = "github"

    def __init__(
        self,
        client: AsyncPlatformApiClient,
        sources: list[str],
        manifest_path: Path,
        *,
        redactor: SecretRedactor | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        concurrency: int = 8,
    ) -> None:
        self._client = client
        self._sources = [self._parse(s) for s in sources]
        self._manifest = manifest_path
        self._redactor = redactor or SecretRedactor()
        self._clock = clock
        self._gate = asyncio.Semaphore(concurrency)

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _parse(source: str) -> tuple[str, str, str]:
        repo_part, _, prefix = source.partition(":")
        org, _, repo = repo_part.partition("/")
        if not repo:
            raise ConfigurationError(f"GitHub knowledge source '{source}' must be org/repo[:path].")
        validate_name(org, "organisation")
        validate_name(repo, "repository name")
        return org, repo, prefix.strip("/")

    async def _files(self, org: str, repo: str, prefix: str) -> list[tuple[str, str]]:
        """(path, blob sha) for markdown files under `prefix` on the default branch."""
        info = await self._client.get(f"/repos/{org}/{repo}")
        branch = info.get("default_branch") or "main"
        tree = await self._client.get(
            f"/repos/{org}/{repo}/git/trees/{quote(branch, safe='')}", params={"recursive": "1"}
        )
        files = [
            (e["path"], e["sha"])
            for e in tree.get("tree", [])
            if e.get("type") == "blob"
            and e["path"].lower().endswith(".md")
            and (not prefix or e["path"].startswith(prefix + "/"))
            and int(e.get("size", 0)) <= MAX_DOC_BYTES
        ]
        return sorted(files)[:MAX_REMOTE_DOCS]

    async def _fetch(self, org: str, repo: str, path: str) -> str:
        async with self._gate:
            return await self._client.get_text(
                f"/repos/{org}/{repo}/contents/{quote(path)}",
                headers={"Accept": "application/vnd.github.raw+json"},
            )

    @staticmethod
    def _id(org: str, repo: str, path: str) -> str:
        return f"github:{org}/{repo}:{path}"

    async def _all_files(self) -> dict[str, tuple[str, str, str, str]]:
        found: dict[str, tuple[str, str, str, str]] = {}
        for org, repo, prefix in self._sources:
            for path, sha in await self._files(org, repo, prefix):
                found[self._id(org, repo, path)] = (org, repo, path, sha)
        return found

    async def search(self, query: str, *, limit: int = 5) -> list[KnowledgeHit]:
        files = await self._all_files()
        texts = await asyncio.gather(*(self._fetch(o, r, p) for o, r, p, _ in files.values()))
        docs = [
            (doc_id, markdown_title(text, Path(v[2]).stem), text)
            for (doc_id, v), text in zip(files.items(), texts, strict=True)
        ]
        return rank(query, docs, limit, self._redactor)

    async def get(self, doc_id: str) -> KnowledgeDocument | None:
        found = (await self._all_files()).get(doc_id)  # only listed documents can be read
        if found is None:
            return None
        org, repo, path, _ = found
        text = await self._fetch(org, repo, path)
        return KnowledgeDocument(
            id=doc_id,
            title=markdown_title(text, Path(path).stem),
            content=self._redactor.redact_text(text),
        )

    async def sync(self) -> KnowledgeStatus:
        files = await self._all_files()
        synced_at = self._clock().isoformat()
        self._manifest.parent.mkdir(parents=True, exist_ok=True)
        entries = {k: v[3] for k, v in files.items()}
        self._manifest.write_text(json.dumps({"synced_at": synced_at, "docs": entries}), "utf-8")
        with contextlib.suppress(OSError):
            os.chmod(self._manifest, 0o600)
        return KnowledgeStatus(provider=self.name, documents=len(files), last_sync=synced_at)

    async def status(self) -> KnowledgeStatus:
        files = {k: v[3] for k, v in (await self._all_files()).items()}
        try:
            saved = json.loads(self._manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return KnowledgeStatus(provider=self.name, documents=len(files), stale=len(files))
        docs = saved.get("docs", {})
        stale = sum(1 for k, v in files.items() if docs.get(k) != v) + sum(
            1 for k in docs if k not in files
        )
        return KnowledgeStatus(
            provider=self.name,
            documents=len(files),
            last_sync=str(saved.get("synced_at") or "") or None,
            stale=stale,
        )


# ---- Confluence -------------------------------------------------------------------------
def basic_auth_from_env(environ: Mapping[str, str]) -> Callable[[], str]:
    """Confluence Cloud: CONFLUENCE_EMAIL + CONFLUENCE_API_TOKEN (Basic authentication)."""

    def provider() -> str:
        email, token = environ.get("CONFLUENCE_EMAIL"), environ.get("CONFLUENCE_API_TOKEN")
        if not email or not token:
            raise AuthenticationError(
                "Confluence credentials are missing.",
                hint="Set CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN.",
            )
        return base64.b64encode(f"{email}:{token}".encode()).decode("ascii")

    return provider


class ConfluenceKnowledgeProvider:
    """Live search of Confluence Cloud pages (no local index)."""

    name = "confluence"

    def __init__(
        self,
        client: AsyncPlatformApiClient,
        *,
        spaces: list[str],
        redactor: SecretRedactor | None = None,
    ) -> None:
        for space in spaces:
            if not _SPACE.match(space):
                raise ConfigurationError(f"Invalid Confluence space key '{space[:30]}'.")
        self._client = client
        self._spaces = spaces
        self._redactor = redactor or SecretRedactor()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _cql(self, query: str) -> str:
        text = re.sub(r'["\\]', " ", query).strip()
        cql = f'type=page AND text ~ "{text}"'
        if self._spaces:
            cql += " AND space in (" + ",".join(f'"{s}"' for s in self._spaces) + ")"
        return cql

    async def search(self, query: str, *, limit: int = 5) -> list[KnowledgeHit]:
        if not query.strip():
            return []
        data = await self._client.get(
            "/wiki/rest/api/search", params={"cql": self._cql(query), "limit": limit}
        )
        hits: list[KnowledgeHit] = []
        for rank_index, item in enumerate(data.get("results", [])[:limit]):
            content = item.get("content") or {}
            if not content.get("id"):
                continue
            hits.append(
                KnowledgeHit(
                    id=f"confluence:{content['id']}",
                    title=item.get("title") or content.get("title") or "Untitled",
                    score=round(1 / (1 + rank_index), 3),  # the service returns results best-first
                    snippet=self._redactor.redact_text(html_to_text(item.get("excerpt", ""))[:200]),
                )
            )
        return hits

    async def get(self, doc_id: str) -> KnowledgeDocument | None:
        page_id = doc_id.removeprefix("confluence:")
        if not page_id.isdigit():
            return None
        try:
            data = await self._client.get(
                f"/wiki/rest/api/content/{page_id}", params={"expand": "body.storage"}
            )
        except ResourceNotFoundError:
            return None
        html = ((data.get("body") or {}).get("storage") or {}).get("value", "")
        return KnowledgeDocument(
            id=doc_id,
            title=data.get("title", "Untitled"),
            content=self._redactor.redact_text(html_to_text(html)),
        )

    async def sync(self) -> KnowledgeStatus:
        return await self.status()  # live source: nothing to synchronise

    async def status(self) -> KnowledgeStatus:
        await self._client.get("/wiki/rest/api/space", params={"limit": 1})  # connectivity check
        return KnowledgeStatus(
            provider=self.name, documents=0, detail="live search; no local index"
        )


# ---- SharePoint (Microsoft Graph) -------------------------------------------------------
async def _default_download(url: str) -> bytes:
    require_secure_url(url, "download URL")
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as http:
        response = await http.get(url)  # pre-authenticated URL: no credentials are sent
        response.raise_for_status()
        if len(response.content) > MAX_DOC_BYTES:
            raise ValidationError("Document is too large.")
        return response.content


class SharePointKnowledgeProvider:
    """SharePoint document libraries via Microsoft Graph (text documents: .md .txt .html).
    Credentials: an OAuth access token in SHAREPOINT_TOKEN or MS_GRAPH_TOKEN with
    Sites.Read.All / Files.Read.All. Site format: `contoso.sharepoint.com:/sites/Engineering`."""

    name = "sharepoint"

    def __init__(
        self,
        client: AsyncPlatformApiClient,
        sites: list[str],
        *,
        download: Callable[[str], Awaitable[bytes]] = _default_download,
        redactor: SecretRedactor | None = None,
    ) -> None:
        for site in sites:
            if not _SITE.match(site):
                raise ConfigurationError(f"Invalid SharePoint site '{site[:60]}'.")
        self._client = client
        self._sites = sites
        self._download = download
        self._redactor = redactor or SecretRedactor()
        self._site_ids: dict[str, str] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _site_id(self, site: str) -> str:
        if site not in self._site_ids:
            data = await self._client.get(f"/v1.0/sites/{site}")
            self._site_ids[site] = str(data["id"])
        return self._site_ids[site]

    async def search(self, query: str, *, limit: int = 5) -> list[KnowledgeHit]:
        term = query.strip().replace("'", "''")
        if not term:
            return []
        hits: list[KnowledgeHit] = []
        for site in self._sites:
            sid = await self._site_id(site)
            data = await self._client.get(
                f"/v1.0/sites/{sid}/drive/root/search(q='{quote(term, safe='')}')"
            )
            for item in data.get("value", []):
                name = item.get("name", "")
                if "file" not in item or not name.lower().endswith(_TEXT_TYPES):
                    continue
                hits.append(
                    KnowledgeHit(
                        id=f"sharepoint:{sid}/{item['id']}",
                        title=name,
                        score=round(1 / (1 + len(hits)), 3),
                        snippet=self._redactor.redact_text(str(item.get("description", ""))[:200]),
                    )
                )
        return hits[:limit]

    async def get(self, doc_id: str) -> KnowledgeDocument | None:
        body = doc_id.removeprefix("sharepoint:")
        sid, _, item_id = body.rpartition("/")
        if not sid or not re.fullmatch(r"[A-Za-z0-9!_\-]+", item_id):
            return None
        try:
            meta = await self._client.get(
                f"/v1.0/sites/{sid}/drive/items/{item_id}",
                params={"select": "name,@microsoft.graph.downloadUrl"},
            )
        except ResourceNotFoundError:
            return None
        name = meta.get("name", "")
        url = meta.get("@microsoft.graph.downloadUrl")
        if not url or not name.lower().endswith(_TEXT_TYPES):
            raise ValidationError("Only text documents (.md, .txt, .html) can be read.")
        raw = (await self._download(url)).decode("utf-8", errors="replace")
        text = html_to_text(raw) if name.lower().endswith((".html", ".htm")) else raw
        return KnowledgeDocument(id=doc_id, title=name, content=self._redactor.redact_text(text))

    async def sync(self) -> KnowledgeStatus:
        return await self.status()

    async def status(self) -> KnowledgeStatus:
        for site in self._sites:
            await self._site_id(site)  # verifies access to each configured site
        return KnowledgeStatus(
            provider=self.name, documents=0, detail="live search; no local index"
        )


def env_token(environ: Mapping[str, str], *names: str) -> Callable[[], str]:
    def provider() -> str:
        for name in names:
            if environ.get(name):
                return environ[name]
        raise AuthenticationError("Access token is missing.", hint=f"Set {' or '.join(names)}.")

    return provider
