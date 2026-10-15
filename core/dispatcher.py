"""
Concurrent Queue Dispatcher for MULTI_DOWNLOADER.
Orchestrates parallel async download workers, enforces per-engine concurrency
limits (e.g. Telegram session protection and FFmpeg CPU caps), renders atomic
multi-stream progress bars, and generates post-batch summary cards.
"""

import asyncio
from collections import Counter
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from core.archive import ArchiveManager
from core.clipboard import play_chime, save_failed_urls
from core.config import ConfigManager
from core.models import BatchSummaryReport, DownloadProgress, DownloadTask, QueueItem, TaskStatus
from core.queue_manager import QueueManager, extract_url_domain
from core.router import URLRouter
from core.terminal import MultiBarManager, Style, print_batch_summary


class QueueDispatcher:
    """Manages concurrent worker execution over the download queue."""

    def __init__(
        self,
        config: Optional[ConfigManager] = None,
        queue_manager: Optional[QueueManager] = None,
        archive: Optional[ArchiveManager] = None,
        router: Optional[URLRouter] = None,
    ):
        self.config = config or ConfigManager()
        self.queue_manager = queue_manager or QueueManager()
        self.archive = archive or ArchiveManager(self.config.archive_db_path)
        self.router = router or URLRouter.create_default(config=self.config)

    async def _execute_item(
        self,
        item: QueueItem,
        slot_id: int,
        multi_bar: MultiBarManager,
    ) -> Tuple[bool, bool, int, Optional[str], Optional[str]]:
        """
        Executes a single download task inside its allocated visual slot.
        Returns: (is_success, is_skipped, file_bytes, error_message, saved_path)
        """
        target_dir = self.config.download_dir
        if self.config.get("general", "organize_by_backend", True):
            target_dir = target_dir / (item.backend or "misc")
        if self.config.get("general", "organize_by_site", True) and item.backend != "gallery-dl":
            host_name = URLRouter.get_host_identifier(item.url, item.backend or "")
            target_dir = target_dir / host_name

        opts = dict(item.options or {})
        opts["quiet"] = True
        opts["concurrent"] = True

        # Central archive deduplication check (for single media items)
        archive_enabled = getattr(self.config, "archive_enabled", None)
        if archive_enabled is None:
            archive_enabled = bool(self.config.get("archive", "enabled", True))
        dedup_by_url = bool(self.config.get("archive", "dedup_by_url", True))
        force_download = bool(opts.get("force") or opts.get("no_archive"))
        is_container = URLRouter.is_container_url(item.url, item.backend)

        if archive_enabled and dedup_by_url and not force_download and not is_container:
            existing_entry = self.archive.get_by_url(item.url, item.backend)
            if existing_entry:
                verify_exists = bool(self.config.get("archive", "verify_file_exists", True))
                file_on_disk = Path(existing_entry.file_path).exists()
                if not verify_exists or file_on_disk:
                    clean_display = existing_entry.file_name or Path(existing_entry.file_path).name
                    self.queue_manager.mark_status(item.id, TaskStatus.SKIPPED)
                    multi_bar.finish_slot(slot_id, f"~{clean_display}", status_type="skipped")
                    return True, True, 0, None, clean_display

        task = DownloadTask(
            url=item.url,
            backend=item.backend or "auto",
            output_dir=str(target_dir),
            options=opts,
        )

        def _progress_cb(prog: DownloadProgress):
            try:
                multi_bar.update_slot(
                    slot_id=slot_id,
                    percent=prog.percent,
                    downloaded=prog.downloaded_bytes,
                    total=prog.total_bytes,
                    speed=prog.speed_bytes_sec,
                    title=prog.current_file,
                    file_index=prog.file_index,
                    total_files=prog.total_files,
                    status_message=prog.status_message,
                    extractor=prog.extractor,
                    container_title=prog.container_title,
                )
            except Exception:
                pass

        try:
            backend_instance = self.router.route(item.url, backend_override=item.backend)
            entry = await backend_instance.download(task, progress_callback=_progress_cb)

            raw_path = Path(entry.file_path)
            try:
                rel_path = raw_path.relative_to(self.config.download_dir.parent)
                clean_display_path = f"~{rel_path}"
            except ValueError:
                clean_display_path = f"~downloads\\{raw_path.name}"

            all_skipped = bool((entry.metadata or {}).get("all_skipped", False))
            if all_skipped:
                self.queue_manager.mark_status(item.id, TaskStatus.SKIPPED)
                multi_bar.finish_slot(slot_id, clean_display_path, status_type="skipped")
                return True, True, 0, None, clean_display_path

            self.queue_manager.mark_status(item.id, TaskStatus.COMPLETED)
            multi_bar.finish_slot(slot_id, clean_display_path, status_type="completed")

            # Record into SQLite deduplication archive if enabled
            if archive_enabled and not opts.get("no_archive"):
                try:
                    entry.file_hash = ArchiveManager.calculate_file_hash(Path(entry.file_path))
                    self.archive.add_entry(entry, upsert=True)
                except Exception:
                    pass

            file_size = entry.file_size or 0
            if not file_size and Path(entry.file_path).exists():
                try:
                    p = Path(entry.file_path)
                    if p.is_file():
                        file_size = p.stat().st_size
                    elif p.is_dir():
                        file_size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                except Exception:
                    file_size = 0

            return True, False, file_size, None, clean_display_path

        except Exception as e:
            err_msg = str(e)
            self.queue_manager.mark_status(item.id, TaskStatus.FAILED, error=err_msg)
            multi_bar.finish_slot(slot_id, item.url, status_type="failed", error_msg=err_msg)
            return False, False, 0, err_msg, None

    def _sync_pending_display(self, multi_bar: MultiBarManager) -> None:
        """Feeds upcoming unstarted queue items to MultiBarManager for stationary rendering."""
        try:
            pending = [
                (item.backend or "auto", item.url)
                for item in self.queue_manager.get_pending_items()
            ]
            multi_bar.set_pending_items(pending)
        except Exception:
            pass

    async def run(
        self,
        urls: Optional[List[str]] = None,
        concurrency: Optional[int] = None,
        options: Optional[Dict] = None,
    ) -> BatchSummaryReport:
        """
        Runs concurrent workers over the queue with engine slot safety constraints.
        If urls is provided, ingests and pre-routes them before starting.
        """
        # Batch link ingestion with pre-routing
        if urls:
            self.queue_manager.add_batch(urls, options=options)

        max_concurrent = concurrency or self.config.get("general", "concurrent_downloads", 3)
        max_concurrent = max(1, min(max_concurrent, 16))

        try:
            raw_limits = getattr(self.config, "engine_limits", None)
            engine_limits = dict(raw_limits) if isinstance(raw_limits, dict) else {"yt-dlp": 2, "telegram-dl": 1}
        except Exception:
            engine_limits = {"yt-dlp": 2, "telegram-dl": 1}

        max_yt = engine_limits.get("yt-dlp", 2)
        max_tg = engine_limits.get("telegram-dl", 1)

        try:
            max_domain_fn = getattr(self.config, "get_max_per_domain", None)
            raw_dom = max_domain_fn() if callable(max_domain_fn) else None
            max_domain = int(raw_dom) if isinstance(raw_dom, (int, float)) else 999
        except Exception:
            max_domain = 999

        # Print banner BEFORE creating or displaying canvas
        if max_concurrent > 1:
            print(f"\n{Style.tag('🚀', 'CONCURRENT', Style.CYAN)} Starting worker pool with {Style.bold(str(max_concurrent))} concurrent streams...")
            guard_parts = [f"Telegram (max {max_tg})", f"FFmpeg/yt-dlp (max {max_yt})"]
            if max_domain < 999:
                guard_parts.append(f"Domain cap (max {max_domain}/host)")
            print(f"{Style.dim('   Engine guards active: ' + ', '.join(guard_parts))}\n")
        else:
            print(f"\n{Style.tag('🚀', 'DOWNLOADING', Style.BLUE)} Starting transfer...\n")

        # Pre-initialize yt-dlp and gallery-dl extractors serially to eliminate multi-thread collision
        try:
            from backends.ytdlp_backend import YtDlpBackend
            YtDlpBackend.pre_initialize()
        except Exception:
            pass

        try:
            from backends.gallerydl_backend import GalleryDlBackend
            GalleryDlBackend.pre_initialize()
        except Exception:
            pass

        total_batch_items = self.queue_manager.count_remaining()

        multi_bar = MultiBarManager(max_slots=max_concurrent)
        self._sync_pending_display(multi_bar)
        multi_bar.set_queue_counts(remaining=total_batch_items, completed=0, failed=0)

        start_time = time.time()
        succeeded = 0
        skipped = 0
        failed = 0
        total_bytes = 0
        total_processed = 0
        failed_items: List[Tuple[str, str]] = []
        saved_paths: List[str] = []

        active_engine_counts: Dict[str, int] = Counter()
        active_domain_counts: Dict[str, int] = Counter()
        active_tasks: Set[asyncio.Task] = set()

        while True:
            # Dispatch new tasks while slots are open
            while len(active_tasks) < max_concurrent:
                item = self.queue_manager.get_next_eligible(
                    active_engine_counts=active_engine_counts,
                    engine_limits=engine_limits,
                    active_domain_counts=active_domain_counts,
                    max_per_domain=max_domain,
                )
                if not item:
                    break

                self.queue_manager.mark_status(item.id, TaskStatus.DOWNLOADING)
                engine_name = item.backend or "auto"
                domain_name = extract_url_domain(item.url)
                active_engine_counts[engine_name] = active_engine_counts.get(engine_name, 0) + 1
                if domain_name:
                    active_domain_counts[domain_name] = active_domain_counts.get(domain_name, 0) + 1

                # Inject global proxy fallback if task options did not specify one
                global_proxy = getattr(self.config, "proxy", None)
                if isinstance(global_proxy, str) and global_proxy and not item.options.get("proxy"):
                    item.options["proxy"] = global_proxy

                slot_id = multi_bar.assign_slot(item.id, engine_name, item.url)
                rem = max(0, total_batch_items - (succeeded + skipped + failed))
                self._sync_pending_display(multi_bar)
                multi_bar.set_queue_counts(
                    remaining=rem,
                    completed=succeeded + skipped,
                    failed=failed,
                )

                async def _task_wrapper(q_item=item, sid=slot_id, eng=engine_name, dom=domain_name):
                    try:
                        ok, was_sk, sz, er, saved_p = await self._execute_item(q_item, sid, multi_bar)
                        return ok, was_sk, sz, er, q_item.url, saved_p
                    finally:
                        active_engine_counts[eng] = max(0, active_engine_counts.get(eng, 1) - 1)
                        if dom:
                            active_domain_counts[dom] = max(0, active_domain_counts.get(dom, 1) - 1)

                t = asyncio.create_task(_task_wrapper())
                active_tasks.add(t)

            if not active_tasks:
                # No running tasks and no eligible items in queue
                break

            # Wait for at least one worker to finish
            done, active_tasks = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)

            for finished_task in done:
                total_processed += 1
                try:
                    ok, was_skip, b_size, err, item_url, saved_p = finished_task.result()
                    if ok:
                        if was_skip:
                            skipped += 1
                        else:
                            succeeded += 1
                            total_bytes += b_size
                        if saved_p:
                            saved_paths.append(saved_p)
                    else:
                        failed += 1
                        if err:
                            failed_items.append((item_url, err))
                except Exception as e:
                    failed += 1
                    failed_items.append(("Task", str(e)))

                rem = max(0, total_batch_items - (succeeded + skipped + failed))
                self._sync_pending_display(multi_bar)
                multi_bar.set_queue_counts(
                    remaining=rem,
                    completed=succeeded + skipped,
                    failed=failed,
                )

        # Clean up multi-bar canvas
        multi_bar.close()

        elapsed_time = time.time() - start_time
        report = BatchSummaryReport(
            total_items=total_processed,
            succeeded_count=succeeded,
            skipped_count=skipped,
            failed_count=failed,
            total_bytes=total_bytes,
            elapsed_seconds=elapsed_time,
            failed_items=[{"url": u, "error": e} for u, e in failed_items],
        )

        # Print post-download summary report (single or batch)
        last_saved = saved_paths[0] if saved_paths else None
        print_batch_summary(
            total_items=total_processed,
            succeeded_count=succeeded,
            skipped_count=skipped,
            failed_count=failed,
            total_bytes=total_bytes,
            elapsed_seconds=elapsed_time,
            failed_items=failed_items,
            saved_path=last_saved,
            saved_paths=saved_paths,
        )

        if failed_items:
            save_failed_urls([u for u, _ in failed_items if u != "Task"])

        silent_run = any(
            bool((item.options or {}).get("no_sound"))
            for item in self.queue_manager.list_all()
        )
        if succeeded > 0 and not silent_run and getattr(self.config, "notifications_enabled", self.config.get("general", "play_sound", True)):
            play_chime()

        return report

    async def run_live_watcher(
        self,
        watcher: Any,
        stop_event: asyncio.Event,
    ) -> None:
        """
        Continuous, non-blocking real-time clipboard watcher mode.
        Concurrently polls system clipboard in background without freezing,
        queues incoming URLs, and dispatches them across active worker slots.
        """
        try:
            from backends.gallerydl_backend import GalleryDlBackend
            GalleryDlBackend.pre_initialize()
        except Exception:
            pass

        max_concurrent = max(1, int(self.config.get("general", "concurrent_downloads", 3)))
        multi_bar = MultiBarManager(max_slots=max_concurrent)
        self._sync_pending_display(multi_bar)

        active_engine_counts: Dict[str, int] = Counter()
        active_tasks: Set[asyncio.Task] = set()
        new_item_event = asyncio.Event()

        succeeded = 0
        skipped = 0
        failed = 0
        total_bytes = 0
        failed_items: List[Tuple[str, str]] = []

        # Background clipboard poller task
        async def _poller_task():
            while not stop_event.is_set():
                new_urls = watcher.poll_new_urls()
                if new_urls:
                    for u in new_urls:
                        eng_name = URLRouter.detect_backend_name(u)
                        self.queue_manager.add(u, backend=eng_name)

                    q_pending = self.queue_manager.count_remaining()
                    active_count = len(active_tasks)
                    rem = q_pending + active_count
                    self._sync_pending_display(multi_bar)
                    multi_bar.set_queue_counts(
                        remaining=rem,
                        completed=succeeded + skipped,
                        failed=failed,
                    )
                    new_item_event.set()

                await asyncio.sleep(0.3)

        poller_task = asyncio.create_task(_poller_task())

        try:
            while not stop_event.is_set():
                # 1. Dispatch tasks to open worker slots
                while len(active_tasks) < max_concurrent:
                    item = self.queue_manager.get_next_eligible(active_engine_counts)
                    if not item:
                        break

                    self.queue_manager.mark_status(item.id, TaskStatus.DOWNLOADING)
                    eng = item.backend or "auto"
                    active_engine_counts[eng] = active_engine_counts.get(eng, 0) + 1
                    slot_id = multi_bar.assign_slot(item.id, eng, item.url)
                    rem = self.queue_manager.count_remaining() + len(active_tasks) + 1
                    self._sync_pending_display(multi_bar)
                    multi_bar.set_queue_counts(
                        remaining=rem,
                        completed=succeeded + skipped,
                        failed=failed,
                    )

                    async def _task_wrapper(q_item=item, sid=slot_id, b_eng=eng):
                        try:
                            ok, was_sk, sz, er, disp_p = await self._execute_item(q_item, sid, multi_bar)
                            return ok, was_sk, sz, er, q_item.url, disp_p
                        finally:
                            active_engine_counts[b_eng] = max(0, active_engine_counts.get(b_eng, 1) - 1)

                    t = asyncio.create_task(_task_wrapper())
                    active_tasks.add(t)

                # 2. Wait for either a worker to complete OR a new link to be copied OR timeout
                if active_tasks:
                    event_waiter = asyncio.create_task(new_item_event.wait())
                    done, pending = await asyncio.wait(
                        list(active_tasks) + [event_waiter],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if event_waiter in done:
                        new_item_event.clear()
                    else:
                        event_waiter.cancel()

                    for task in done:
                        if task in active_tasks:
                            active_tasks.remove(task)
                            try:
                                ok, was_skip, b_size, err, item_url, disp_p = task.result()
                                if ok:
                                    if was_skip:
                                        skipped += 1
                                    else:
                                        succeeded += 1
                                        total_bytes += b_size
                                else:
                                    failed += 1
                                    if err:
                                        failed_items.append((item_url, err))
                            except Exception as e:
                                failed += 1
                                failed_items.append(("Task", str(e)))

                            rem = self.queue_manager.count_remaining() + len(active_tasks)
                            self._sync_pending_display(multi_bar)
                            multi_bar.set_queue_counts(
                                remaining=rem,
                                completed=succeeded + skipped,
                                failed=failed,
                            )
                else:
                    # Idle: wait for poller to signal a new item
                    try:
                        await asyncio.wait_for(new_item_event.wait(), timeout=0.25)
                        new_item_event.clear()
                    except asyncio.TimeoutError:
                        pass

        finally:
            poller_task.cancel()
            for t in list(active_tasks):
                t.cancel()
            multi_bar.close()

            if failed_items:
                save_failed_urls([u for u, _ in failed_items if u != "Task"])

            if succeeded > 0 and getattr(self.config, "notifications_enabled", self.config.get("general", "play_sound", True)):
                play_chime()
