"""Downloads videos of favourited posts into a local folder."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

EXTENSIONS = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}


class CacheError(RuntimeError):
    pass


class VideoCache:
    """Saves one video file per favourite as `<platform>-<id>.<ext>`.

    Downloads stream to a `.part` file and are renamed when complete, so a
    half-finished download is never served. At most `concurrency` downloads
    run at once so favouriting many posts does not saturate the connection.
    """

    def __init__(self, folder: str | Path, client: httpx.AsyncClient, max_mb: int = 500, concurrency: int = 2):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.max_bytes = max_mb * 1024 * 1024
        self._slots = asyncio.Semaphore(concurrency)

    @staticmethod
    def stem(platform: str, post_id: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "_", f"{platform}-{post_id}")[:120]

    def path_for(self, filename: str) -> Path:
        return self.folder / filename

    async def download(self, platform: str, post_id: str, url: str) -> tuple[str, int]:
        """Download `url`; return (filename, size in bytes)."""
        if not url.lower().startswith(("http://", "https://")):
            raise CacheError("Only http(s) video links can be cached")
        stem = self.stem(platform, post_id)
        part = self.folder / f"{stem}.part"
        async with self._slots:
            try:
                async with self.client.stream("GET", url, follow_redirects=True, timeout=60) as r:
                    if r.status_code >= 400:
                        raise CacheError(f"HTTP {r.status_code} downloading video")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if not (ctype.startswith("video/") or ctype in ("application/octet-stream", "binary/octet-stream")):
                        raise CacheError(f"Link is not a video ({ctype or 'unknown type'})")
                    declared = int(r.headers.get("content-length") or 0)
                    if declared > self.max_bytes:
                        raise CacheError("Video is larger than the cache size limit")
                    size = 0
                    with part.open("wb") as f:
                        async for chunk in r.aiter_bytes(256 * 1024):
                            size += len(chunk)
                            if size > self.max_bytes:
                                raise CacheError("Video is larger than the cache size limit")
                            f.write(chunk)
                filename = stem + EXTENSIONS.get(ctype, ".mp4")
                os.replace(part, self.folder / filename)
                return filename, size
            except httpx.HTTPError as e:
                raise CacheError(f"Download failed: {e}") from e
            finally:
                part.unlink(missing_ok=True)

    def remove(self, filename: str | None) -> None:
        if filename:
            (self.folder / Path(filename).name).unlink(missing_ok=True)

    def usage(self) -> dict:
        files = [p for p in self.folder.iterdir() if p.is_file() and p.suffix != ".part"]
        return {"folder": str(self.folder.resolve()), "files": len(files), "bytes": sum(p.stat().st_size for p in files)}
