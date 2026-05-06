"""Run an async coroutine from sync code, in any context.

Used by the sync wrappers around ``receiver.aevaluate``, ``ascore``, and
``ascore_question`` so callers in Jupyter / IPython kernels (where an event
loop is already running) can invoke them like regular Python functions —
without ``asyncio.run`` or ``await``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Coroutine


def run_sync(coro: Coroutine) -> Any:
    """Drive ``coro`` to completion from sync code.

    - **No running loop** (regular Python script, plain shell): uses
      ``asyncio.run`` directly.
    - **Running loop** (Jupyter notebook, IPython kernel, inside another
      async function): runs ``coro`` in a fresh thread with its own loop
      and blocks until it finishes. This avoids the
      ``RuntimeError: asyncio.run() cannot be called from a running event
      loop`` that bare ``asyncio.run`` raises in those contexts.

    The caller is the same either way::

        receiver.evaluate()  # works in terminal AND in Jupyter
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
