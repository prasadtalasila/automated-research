"""The child half of tests/test_runlock.py's cross-process cases.

Its own module, and deliberately a tiny one: `multiprocessing`'s spawn
start method reconstructs the target by *importing the module it lives
in*, so anything this file imports is imported again in a fresh
interpreter, on top of a `sys.path` inherited from the parent.

That inheritance is the problem this module exists to avoid. By the time
the suite reaches the runlock tests, the parent has imported docling,
which imports OpenCV, which puts its own package directory on `sys.path`
-- where `cv2/typing/` shadows the standard library's `typing`. A child
that imports pytest (as it did when the target lived in the test module)
therefore reaches `from typing import Any`, gets `cv2.typing`, and
imports `cv2.dnn`, which needs `libGL.so.1`. On a host without that
library the child dies before it can take the lock, the parent waits out
its timeout, and the suite stalls -- see issue #45.

Keeping the child's import graph to `time` plus `src.runlock` makes it
independent of whatever the parent happened to import.
"""

import time

from src import runlock

# How long the child keeps the lock if nothing kills it. Long enough that
# no test races it, short enough that a leaked child cannot outlive the
# session.
HELD_FOR = 120


def hold(path, started):
    """Take the lock, signal, and hold it until killed.

    Deliberately not woken by a second Event. `multiprocessing.Event` is a
    Condition behind a POSIX semaphore, and a process killed while inside
    `wait()` dies holding that semaphore -- the kernel reclaims file
    descriptors on death, but not semaphores, so the parent's `set()`
    would block forever. The parent ends this child by killing it, which
    needs no synchronisation at all.
    """
    with runlock.pipeline_lock(path):
        started.set()
        time.sleep(HELD_FOR)
