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
