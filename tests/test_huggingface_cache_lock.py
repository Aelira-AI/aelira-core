"""Exercise the Hub cache path that consumes filelock at runtime."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from huggingface_hub import hf_hub_download
from huggingface_hub import file_download


def test_parallel_hub_downloads_share_one_cache_write(tmp_path):
    payload = b"aelira-cache-lock-regression"
    commit = "a" * 40
    ready = threading.Barrier(2)
    state_lock = threading.Lock()
    downloads = 0
    active_downloads = 0
    peak_downloads = 0

    def metadata(**_kwargs):
        ready.wait(timeout=5)
        return (
            "https://example.invalid/weights.bin",
            "test-etag",
            commit,
            len(payload),
            None,
            None,
        )

    def http_get(_url, fileobj, **_kwargs):
        nonlocal downloads, active_downloads, peak_downloads
        with state_lock:
            downloads += 1
            active_downloads += 1
            peak_downloads = max(peak_downloads, active_downloads)
        try:
            time.sleep(0.1)
            fileobj.write(payload)
        finally:
            with state_lock:
                active_downloads -= 1

    def download():
        return hf_hub_download(
            repo_id="example/test",
            filename="weights.bin",
            cache_dir=tmp_path,
        )

    with (
        patch.object(file_download, "_get_metadata_or_catch_error", metadata),
        patch.object(file_download, "http_get", http_get),
    ):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(download) for _ in range(2)]
            paths = [future.result(timeout=10) for future in futures]

    assert downloads == 1, f"expected one download, got {downloads}"
    assert peak_downloads == 1
    assert paths[0] == paths[1]
    assert Path(paths[0]).read_bytes() == payload
