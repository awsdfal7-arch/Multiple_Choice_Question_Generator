from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from typing import Any, Callable


def run_callables_in_parallel_fail_fast(*, callables: list[Callable[[], None]], max_workers: int) -> None:
    if not callables:
        return
    executor = ThreadPoolExecutor(max_workers=max(1, max_workers))
    futures = []
    try:
        futures = [executor.submit(fn) for fn in callables]
        for future in as_completed(futures):
            future.result()
    except Exception:
        for future in futures:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def run_tasks_in_parallel(
    *,
    tasks: list[tuple[int, str, str]],
    max_workers: int,
    stop_cb: Callable[[], bool],
    on_task_start: Callable[[int, int, tuple[int, str, str]], None],
    on_task_done: Callable[[tuple[int, str, str], Any], None],
    on_task_failed: Callable[[tuple[int, str, str], Exception], None],
    run_one: Callable[[tuple[int, str, str]], Any],
) -> None:
    total = len(tasks)
    if total <= 0:
        return
    worker_count = max(1, min(int(max_workers), total))
    it = iter(tasks)
    launched = 0
    in_flight: dict[Any, tuple[int, str, str]] = {}

    def submit_next(executor: ThreadPoolExecutor) -> bool:
        nonlocal launched
        if stop_cb():
            return False
        try:
            task = next(it)
        except StopIteration:
            return False
        launched += 1
        on_task_start(launched, total, task)
        future = executor.submit(run_one, task)
        in_flight[future] = task
        return True

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for _ in range(worker_count):
            if not submit_next(executor):
                break
        while in_flight:
            done_futures, _ = wait(tuple(in_flight.keys()), return_when=FIRST_COMPLETED)
            for future in done_futures:
                task = in_flight.pop(future)
                try:
                    result = future.result()
                except Exception as e:
                    on_task_failed(task, e)
                else:
                    on_task_done(task, result)
                if not stop_cb():
                    submit_next(executor)
