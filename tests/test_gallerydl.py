import pytest
from pathlib import Path
from backends.gallerydl_backend import GalleryDlBackend
from gallery_dl import config as gdl_config


def test_gallerydl_can_handle():
    backend = GalleryDlBackend()
    assert backend.can_handle("https://twitter.com/jack/status/20") is True
    assert backend.can_handle("https://x.com/jack/status/20") is True
    assert backend.can_handle("https://www.reddit.com/r/aww/comments/12345/cute_dog/") is True
    assert backend.can_handle("https://imgur.com/gallery/abcde") is True
    assert backend.can_handle("https://danbooru.donmai.us/posts/12345") is True
    # Should not handle telegram
    assert backend.can_handle("https://t.me/c/12345/678") is False


def test_gallerydl_modular_config_loading(tmp_path):
    backend = GalleryDlBackend({
        "config_file": "configs/gallery-dl.json"
    })
    
    test_out = tmp_path / "downloads" / "gallery-dl" / "reddit"
    backend._configure_job(out_dir=test_out, disable_archive=True)
    
    # Verify modular config values from gallery-dl.json were loaded
    assert gdl_config.get(("extractor", "civitai"), "metadata") is True
    assert gdl_config.get(("extractor",), "retries") == 11
    
    # Verify base-directory was overridden to target dir
    assert gdl_config.get(("extractor",), "base-directory") == str(test_out.resolve())
    
    # Verify archive was disabled for test isolation
    assert gdl_config.get(("extractor",), "archive") is None


def test_gallerydl_batch_tracker(tmp_path):
    from backends.gallerydl_backend import _GalleryDlBatchTracker

    tracker = _GalleryDlBatchTracker()
    assert tracker.total_files is None
    assert tracker.completed_count == 0

    # 1. On directory metadata
    tracker.on_directory({"count": 50, "title": "Sample Gallery"})
    assert tracker.total_files == 50
    assert tracker.title_announced is True

    # 2. On URL queued
    tracker.on_url("https://example.com/001.jpg", {"num": 1, "filename": "001.jpg"})
    assert tracker.current_filename == "001.jpg"

    # 3. On start & progress
    dummy_file = tmp_path / "001.jpg"
    dummy_file.write_bytes(b"hello world")

    tracker.start(str(dummy_file))
    assert tracker.current_filename == "001.jpg"

    tracker.progress(1000, 500, 250000)
    assert tracker.current_total == 1000
    assert tracker.current_downloaded == 500
    assert tracker.current_speed == 250000.0

    # 4. On success
    tracker.success(str(dummy_file))
    assert tracker.completed_count == 1
    assert len(tracker.downloaded_files) == 1
    assert sum(tracker._file_bytes.values()) == 11  # len(b"hello world")

    # 5. On skip
    tracker.skip(str(dummy_file))
    assert tracker.completed_count == 2
    assert tracker.skipped_count == 1

    # 6. Finish
    tracker.finish()


