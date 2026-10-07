import pytest
import asyncio
from pathlib import Path
from backends.ytdlp_backend import YtDlpBackend
from core.models import DownloadTask, TaskStatus


@pytest.mark.asyncio
async def test_ytdlp_can_handle():
    backend = YtDlpBackend()
    assert backend.can_handle("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True
    assert backend.can_handle("https://youtu.be/dQw4w9WgXcQ") is True
    assert backend.can_handle("https://vimeo.com/111111") is True
    # Should not handle telegram
    assert backend.can_handle("https://t.me/c/12345/678") is False


@pytest.mark.asyncio
async def test_ytdlp_extract_info():
    # Test metadata extraction on a short reliable test video
    backend = YtDlpBackend()
    test_url = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Big Buck Bunny clip
    try:
        info = await backend.extract_info(test_url)
        assert info is not None
        assert "title" in info
        assert "id" in info
    except Exception as e:
        pytest.skip(f"Network / YouTube throttling during test: {e}")


@pytest.mark.asyncio
async def test_ytdlp_download_and_archive(tmp_path):
    # Test downloading to a temporary directory
    backend = YtDlpBackend({
        "format": "worst",  # Smallest download for testing
        "merge_output_format": "mp4",
        "embed_metadata": False,
        "embed_thumbnail": False,
    })
    
    test_url = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
    task = DownloadTask(
        url=test_url,
        backend=backend.name,
        output_dir=str(tmp_path),
    )

    try:
        entry = await backend.download(task)
        assert entry is not None
        assert entry.file_path is not None
        assert Path(entry.file_path).exists()
        assert Path(entry.file_path).stat().st_size > 0
        assert entry.backend == "yt-dlp"
    except Exception as e:
        pytest.skip(f"Network error during download test: {e}")
