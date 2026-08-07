"""PDF text extraction: dispatches to whichever backend config.PARSER
names (config.toml's [parser].backend, or the PARSER env var) --
"pdftotext" (default) or "docling". Both write into
the same place, content/parsed/<citekey>.txt, so every downstream
consumer (src/ledger.py, src/retrieval.py, scripts/verbatim_check.py)
stays backend-agnostic; only this module needs to know which one is
configured.

pdftotext has no Python dependency (a subprocess call to poppler-utils)
and is the only backend whose output has page boundaries (form-feed
characters between pages) -- docling produces one continuous document.
See config.toml's [parser] comment for the full tradeoffs (speed,
page-boundary loss) before switching off the default.

The dispatch is deliberately a table rather than an if/else: adding a
backend is a `_extract_*` function plus one `_EXTRACTORS` entry, and
markitdown was removed through the same seam (see docs/PDF-PARSER.md for why).
"""

import importlib.util
import multiprocessing
import os
import re
import signal
import sys
import shutil
import subprocess
from pathlib import Path

from src import config, passages

# Logical CPUs one docling worker is *charged*. Used both as the divisor
# for `workers = "auto"` and as the ceiling an explicit request is
# clamped to.
#
# The 4 came from a single docling process measured holding ~300% CPU, on
# the reasoning that "one worker per CPU" would then oversubscribe by
# about 4x.
#
# **A full-corpus sweep does not support it.** Measured over all 501 PDFs
# (docs/PERFORMANCE.md, bench/results/2026-08-04-full-corpus/):
#   - 32 workers is ~1.4x faster than the 12 this constant allows on a
#     48-CPU machine, and 48 workers is no worse;
#   - docling's own num_threads changes a run by 1.9% -- noise -- so a
#     worker is not doing four CPUs of parallel work.
# Past ~32 workers the curve plateaus rather than reversing (32 and 48
# land within 0.9% over three runs each), so the finding is "this divisor
# is much too large", not "it should be 1.5".
#
# Left at 4 deliberately: changing it alters what every run does on every
# machine, and it has been validated on exactly one machine and one
# corpus. See bench/PARALLELISM-PLAN.md's open questions for what would
# have to be measured before moving it.
_CPUS_PER_DOCLING_WORKER = 4

# Docling's own default num_threads. Equal to the divisor above today,
# and deliberately a separate constant: that one is a CPU budget and the
# roadmap expects it to shrink, while this one tracks upstream's default.
# Sharing a literal would have quietly changed the thread cap the first
# time the budget moved.
_DOCLING_DEFAULT_THREADS = 4

# How long a worker gets to honour SIGTERM before being killed outright.
_TERMINATE_GRACE_SECONDS = 2.0

# Distinct docling error messages to quote before summarising the rest.
_MAX_DOCLING_ERRORS = 3

# nvidia-smi normally answers in tens of milliseconds; a driver in a bad
# state is what makes it hang, and that must not hang a sync that would
# otherwise have run on the CPU.
_NVIDIA_SMI_TIMEOUT = 10.0

# Free device memory below which a card is not worth giving a worker.
#
# Measured on this project's own corpus: a docling worker holding the
# layout, table and OCR models sits at ~1.7 GiB of device memory, on top
# of a CUDA context of its own. 2.5 GiB is that figure with room to be
# wrong in the direction that costs a little speed rather than the one
# that costs the run.
_GPU_MIN_FREE_MIB = 2560


def allowed_cpus() -> int:
    """How many CPUs this *process* may run on -- not how many the
    machine has.

    `os.cpu_count()` reports the machine. `os.sched_getaffinity(0)`
    reports the affinity mask actually in force, which a container,
    `taskset`, or a batch scheduler will have narrowed. On the host this
    was developed on the two disagree badly: 96 CPUs exist, 48 are
    permitted, so sizing a pool off `cpu_count()` would spawn twice as
    many workers as there are CPUs to run them.

    `sched_getaffinity` is Linux-only -- it does not exist on Windows or
    macOS, and this project's CI has a windows-latest leg -- hence the
    getattr rather than a bare call.

    Not covered: a cgroup CPU *quota* (`docker --cpus=2`) throttles
    without narrowing the affinity mask, so this still reports the full
    set there. config.toml.example says so, and an explicit
    [parser].workers is the answer on such a host.
    """
    getaffinity = getattr(os, "sched_getaffinity", None)
    if getaffinity is not None:
        return len(getaffinity(0))
    return os.cpu_count() or 1


def worker_ceiling() -> int:
    """The most workers this machine can sustain, whatever the run holds.

    Split out from resolve_workers because it is the one ceiling that
    does *not* depend on how many documents there are, so it can be asked
    before the bibliography has been read -- which is what lets
    prestart_pool decline on a machine that will end up serial anyway.
    """
    cpus = allowed_cpus()
    if config.PARSER == "docling":
        return max(1, cpus // _CPUS_PER_DOCLING_WORKER)
    # Each pdftotext is a short, single-threaded subprocess, so charging
    # it a docling worker's CPU budget would under-use the machine.
    return cpus


def resolve_workers(n_docs: int) -> tuple[int, str | None]:
    """(workers, complaint) for a run that has `n_docs` to parse.

    The resolved count is the smallest of three independent ceilings,
    floored at 1: what was asked for, what the machine can sustain, and
    how many documents there actually are. The third matters more than it
    looks -- standing up 12 docling workers to parse 3 documents pays 12
    model loads to save two documents' worth of work.

    An explicit request above the machine's ceiling is clamped *and
    reported*. Obeying it thrashes; ignoring it silently leaves someone
    believing they configured something they didn't.
    """
    cpus = allowed_cpus()
    ceiling = worker_ceiling()

    requested = config.PARSER_WORKERS
    wanted = ceiling if requested == "auto" else requested
    workers = max(1, min(wanted, ceiling, n_docs or 1))

    complaint = None
    if requested != "auto" and requested > ceiling:
        complaint = (
            f"  WARNING [parser].workers={requested} exceeds what this host can "
            f"sustain ({cpus} CPUs available to this process"
            + (f", ~{_CPUS_PER_DOCLING_WORKER} per docling worker"
               if config.PARSER == "docling" else "")
            + f") -- using {workers}."
        )
    return workers, complaint


def terminate_workers(executor) -> None:
    """Kill the pool's worker processes outright.

    shutdown(cancel_futures=True) only drops jobs that have not started;
    the handful already running keep going, and ProcessPoolExecutor's
    atexit hook then *joins* them on the way out. With docling that is
    minutes per in-flight document, which is what "Ctrl+C took forever to
    exit" actually was.

    There is no public API for this -- ProcessPoolExecutor deliberately
    exposes no way to cancel running work -- so this reaches for
    `_processes`. Guarded with getattr because it is private and because
    a ThreadPoolExecutor (the pdftotext backend, and the tests) has no
    such attribute and needs no killing.
    """
    processes = list(getattr(executor, "_processes", {}).values())
    for process in processes:
        try:
            process.terminate()
        except Exception:  # noqa: BLE001 -- already exited, or already reaped
            pass
    # SIGTERM is a request, and a worker sitting in onnxruntime or torch
    # native code does not necessarily honour it promptly -- measured 21
    # processes still alive after terminate() alone, which would leave
    # orphans holding GPU memory. Give them a moment, then insist.
    for process in processes:
        try:
            process.join(timeout=_TERMINATE_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
        except Exception:  # noqa: BLE001 -- as above
            pass


class interrupt_guard:
    """Exit promptly and cleanly on Ctrl+C while a worker pool is running.

    An `except KeyboardInterrupt` around `as_completed()` does NOT work
    here, and that is the whole reason this exists. Reproduced in a
    minimal script with no docling in it: SIGINT reaches the parent, the
    result loop stops consuming, and the handler never runs -- the
    process then sits until its in-flight workers finish, which with
    docling is minutes per document. That is the reported symptom,
    "Ctrl+C took forever to exit", plus docling teardown tracebacks from
    workers still being driven.

    An explicit SIGINT handler does work: measured 0.0s to exit against
    60s+ (and counting) for the exception path. It terminates the pool's
    workers and calls os._exit, deliberately skipping interpreter
    shutdown -- ProcessPoolExecutor's atexit hook *joins* its workers,
    which is the thing being avoided. Skipping it is safe for this
    program's own state because the ledger commits incrementally and
    synchronously as each document lands: whatever finished is already on
    disk.

    Restores the previous handler on the way out, so a library caller
    that installed its own gets it back.
    """

    def __init__(self, executor, describe):
        self._executor = executor
        self._describe = describe
        self._previous = None

    def __enter__(self):
        try:
            self._previous = signal.signal(signal.SIGINT, self._on_sigint)
        except ValueError:
            # Not the main thread (a test, or an embedding caller):
            # signal handling isn't available, and the pool still works.
            self._previous = None
        return self

    def __exit__(self, *exc_info):
        if self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)
        return False

    def _on_sigint(self, _signum, _frame):
        print(f"\n  interrupted -- {self._describe()}. Work already "
              "finished is kept; re-run to continue.", file=sys.stderr, flush=True)
        terminate_workers(self._executor)
        os._exit(130)


def _parse_visible_devices(total: int) -> "tuple[int, list[int] | None]":
    """(how many devices this process sees, which physical cards they
    are) after CUDA_VISIBLE_DEVICES, which nvidia-smi ignores and every
    CUDA process obeys.

    Without the count, restricting a run to one card would still hand
    worker 3 a `cuda:3` that does not exist in its view, and the worker
    would die on the first convert. torch.cuda.device_count() applies the
    same filter internally, so this is what keeps the nvidia-smi count
    interchangeable with the torch one.

    The second element is the mapping the *count* alone can't give:
    `CUDA_VISIBLE_DEVICES=2,5` makes physical card 2 into `cuda:0` and
    physical card 5 into `cuda:1`, so anything reading per-device figures
    out of nvidia-smi -- which reports physical indices -- has to go
    through it to name the right card. It is None when a UUID was named,
    because resolving one to an index needs torch; usable_devices then
    declines to filter rather than guessing at which card is which.

    Follows CUDA's own documented parsing: enumeration stops at the first
    entry that is not a valid device, so "0,foo,1" means one device, not
    two, and "-1" means none.
    """
    spec = os.environ.get("CUDA_VISIBLE_DEVICES")
    if spec is None:
        return total, list(range(total))
    count, order, resolvable = 0, [], True
    for entry in spec.split(","):
        entry = entry.strip()
        # UUIDs (GPU-..., MIG-...) are accepted as named devices: this
        # cannot check they exist, and over-counting them is no worse
        # than the pre-nvidia-smi behaviour.
        if entry.startswith(("GPU-", "MIG-")):
            count += 1
            resolvable = False
        elif entry.isdigit() and int(entry) < total:
            count += 1
            order.append(int(entry))
        else:
            break
    # Clamped because a UUID cannot be checked against anything: six of
    # them named on a four-card host would otherwise hand out a cuda:5.
    return min(count, total), (order if resolvable else None)


def _visible_devices(total: int) -> int:
    """`total` narrowed by CUDA_VISIBLE_DEVICES."""
    return _parse_visible_devices(total)[0]


def _gpu_free_mib_nvidia_smi() -> "dict[int, int] | None":
    """Free memory per *physical* CUDA device in MiB, or None if
    nvidia-smi can't say.

    Same forgiving contract as _gpu_count_nvidia_smi, and the same reason
    for preferring the driver's own tool to torch: this runs in the
    parent, which must be able to hand a usable CUDA context to a forked
    child, and importing torch here would take that away.
    """
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None
    try:
        result = subprocess.run(
            [smi, "--query-gpu=index,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True,
            timeout=_NVIDIA_SMI_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    free = {}
    for line in result.stdout.splitlines():
        index, _, mib = line.partition(",")
        try:
            free[int(index)] = int(mib)
        except ValueError:
            # A card the driver can't currently report on prints "[N/A]"
            # rather than a number. Leaving it out of the mapping is what
            # makes usable_devices treat it as "can't tell", which is the
            # same answer it gives when nvidia-smi is missing entirely.
            continue
    return free or None


def usable_devices() -> "tuple[list[int], str | None]":
    """(the CUDA device numbers worth giving a worker, a complaint).

    Round-robin over `range(gpu_count())` assumes every card has room for
    a worker. The run this was written for found GPU 0 already holding
    44.4 GiB of a previous run's orphaned workers: the four workers
    assigned to it could not load a model at all, and -- because a worker
    that fails takes about 19s where a working one takes minutes -- those
    four went on to claim, and fail, 334 of the corpus's 456 documents. A
    poisoned worker is not merely useless, it is *faster* than a working
    one, so the pool feeds it preferentially. Skipping a full card up
    front is the difference between a slower run and a ruined one.

    Forgiving in exactly the way gpu_count is: no nvidia-smi, a reading
    it won't give, or a device list this can't map back to physical cards
    all mean "assume every device is usable". Refusing a GPU on the
    strength of a measurement we don't have would be worse than the
    occasional bad assignment, which _extract_docling now recovers from
    anyway.
    """
    n_gpus = gpu_count()
    if n_gpus <= 0:
        return [], None
    free = _gpu_free_mib_nvidia_smi()
    if free is None:
        return list(range(n_gpus)), None
    # nvidia-smi numbers physical cards from 0 without gaps, so the
    # highest index it reported is the last card -- which is what
    # _parse_visible_devices needs to range-check a CUDA_VISIBLE_DEVICES
    # entry. A card omitted above (an "[N/A]" reading) can make this an
    # undercount, which shortens the mapping and so leaves the devices
    # past it unfiltered: the forgiving answer again.
    _, physical = _parse_visible_devices(max(free) + 1)
    if physical is None:
        return list(range(n_gpus)), None
    usable, skipped = [], []
    for device in range(n_gpus):
        mib = free.get(physical[device]) if device < len(physical) else None
        if mib is None or mib >= _GPU_MIN_FREE_MIB:
            usable.append(device)
        else:
            skipped.append(f"cuda:{device} ({mib / 1024:.1f} GiB free)")
    if not skipped:
        return usable, None
    detail = ", ".join(skipped)
    if usable:
        return usable, (
            f"  WARNING skipping {detail} -- under "
            f"{_GPU_MIN_FREE_MIB / 1024:.1f} GiB free, which is not enough for a "
            f"docling worker. Parsing on cuda:"
            + ",".join(str(d) for d in usable) + ".")
    # Every card is full. The CPU is slower -- measured 4.7x over 100
    # documents with OCR off, and 1.8x with it on, since OCR is CPU work
    # either way -- but it is a run that finishes, which is more than the
    # alternative.
    return [], (
        f"  WARNING every GPU is busy ({detail}) -- parsing on the CPU. "
        "Free a card or wait, then re-run to get the GPUs back.")


def _gpu_count_nvidia_smi() -> "int | None":
    """CUDA devices per the driver's own tool, or None if it can't say.

    Preferred over torch because it answers the question without
    importing torch into *this* process: a 1.2s import and ~200MB of RSS
    in a parent that has no other use for it, and -- the reason this
    exists -- a parent that has touched CUDA cannot hand a usable context
    to a forked child. See start_method for what that buys.
    """
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None
    try:
        result = subprocess.run(
            [smi, "--list-gpus"], capture_output=True, text=True,
            timeout=_NVIDIA_SMI_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        # A driver mismatch makes nvidia-smi hang or die rather than
        # print an empty list, and neither is a reason to fail a sync.
        return None
    if result.returncode != 0:
        return None
    return sum(1 for line in result.stdout.splitlines() if line.startswith("GPU "))


def gpu_count() -> int:
    """CUDA devices Docling could use, or 0.

    Deliberately forgiving: no nvidia-smi, no torch, a torch without
    CUDA, or a driver mismatch all mean "no GPUs to spread across", which
    is a perfectly good answer -- not a reason to take down a sync that
    would otherwise have run on the CPU.

    nvidia-smi first, torch second. The fallback matters on a host whose
    driver tools aren't on PATH (a slim container that still passes
    /dev/nvidia* through), where dropping to 0 would silently undo the
    per-worker GPU assignment and put every worker back on cuda:0.
    """
    if config.PARSER != "docling":
        return 0
    counted = _gpu_count_nvidia_smi()
    if counted is not None:
        return _visible_devices(counted)
    try:
        import torch

        return torch.cuda.device_count()
    except Exception:  # noqa: BLE001 -- see docstring: any failure means 0
        return 0


def cuda_is_initialised() -> bool:
    """Has *this* process already got a live CUDA context?

    Checked through sys.modules rather than by importing torch, so asking
    the question can never be what makes the answer true.

    Deliberately about the observed state rather than about who caused
    it: gpu_count is no longer the only candidate (src/enrich/embed_index
    runs sentence-transformers, and a library caller may have done
    anything at all before calling in), and a start method chosen from
    "did anyone touch CUDA" is right in all of those cases while one
    chosen from "did we call device_count" is right in none of them.
    """
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    try:
        return bool(torch.cuda.is_initialized())
    except Exception:  # noqa: BLE001 -- can't tell, so assume the worst
        return True


# Imported once in the forkserver process and inherited by every worker
# it forks, instead of imported separately in each. Measured on the
# documented A40 host: 3.2s of the ~8.5s a cold worker needs to reach its
# first parsed page (1.2s torch, 2.1s docling).
#
# Named submodules rather than bare "docling" because docling's top-level
# package is mostly re-exports -- these are the modules
# _docling_converter actually imports.
_PRELOAD_MODULES = (
    "torch",
    "docling.datamodel.base_models",
    "docling.datamodel.pipeline_options",
    "docling.datamodel.accelerator_options",
    "docling.document_converter",
)


def preload_modules() -> list[str]:
    """_PRELOAD_MODULES, minus anything this machine hasn't got installed.

    Keeps the preload list honest on a pdftotext-only install, where
    naming docling modules would be asking the forkserver to import
    packages that were never installed.

    **This is not a guard against a broken installation**, and it is
    worth being exact about that. `find_spec` only answers "can this
    module be located", not "does importing it work" -- an installed
    torch whose native library fails to load passes this check and then
    raises OSError inside the forkserver, which `forkserver.main()` does
    not swallow (it catches ImportError only). Such a machine gets a dead
    forkserver, and the pool fails when it tries to use it. That is a
    real gap; it is left open because the same installation fails under
    `spawn` too, one worker later, and because probing it properly would
    mean importing torch in the parent -- the exact cost this whole path
    exists to avoid.
    """
    available = []
    for name in _PRELOAD_MODULES:
        try:
            if importlib.util.find_spec(name) is not None:
                available.append(name)
        except (ImportError, ValueError):
            # A namespace-package parent, or a module whose *parent*
            # can't be imported -- either way, not preloadable.
            pass
    return available


def start_method() -> tuple[str, str | None]:
    """(multiprocessing start method for the docling pool, complaint).

    "forkserver" where the platform has it, "spawn" otherwise. The
    difference is one shared import of torch and docling instead of one
    per worker -- 3.2s of the ~8.5s a cold worker needs before its first
    parsed page. The other ~5s is Docling's model load, which no start
    method can share, since `initialized_pipelines` lives on the
    converter instance.

    Choosing forkserver is not on its own worth anything; see
    prestart_pool, which is what turns it into a measured saving.

    **Plain "fork" is not offered**, and the reason is not the one this
    code used to give. The old comment said counting GPUs initialised
    CUDA in the parent, so a forked child would inherit a broken context;
    measured against torch 2.7.1, `torch.cuda.device_count()` goes
    through NVML and leaves `torch.cuda.is_initialized()` False, so that
    hazard was not real here -- and gpu_count no longer imports torch at
    all, so it cannot become real. What rules fork out is the other
    inheritance: this process holds the run lock and the ledger open as
    live sqlite connections, and SQLite's own documentation says not to
    carry an open connection across fork(). A forked worker finalising
    an inherited connection on the way out would be rolling back a
    transaction belonging to a process it is not. forkserver has neither
    problem -- its server is a fresh interpreter, so workers inherit the
    preloaded modules and nothing else -- and it measured *faster* than
    fork (9.6s against 9.7s at four workers), so there is nothing to
    trade off.

    The CUDA check survives anyway, because a caller can initialise CUDA
    in this process by other means (src/enrich/embed_index does), and
    forkserver's own server process is started by the first pool -- which
    is well after that could have happened.
    """
    requested = config.PARSER_START_METHOD
    available = multiprocessing.get_all_start_methods()
    wanted = "forkserver" if requested == "auto" else requested

    if wanted == "spawn":
        return "spawn", None
    if wanted not in available:
        # Windows has spawn and nothing else. Silent under "auto",
        # because picking what the platform has is exactly what "auto"
        # was asked to do; said out loud when the method was named,
        # because otherwise the key reads as honoured and isn't.
        if requested == "auto":
            return "spawn", None
        return "spawn", (
            f"  NOTE [parser].start_method={requested!r} is not available on this "
            f"platform (only {', '.join(available)}) -- using spawn."
        )
    if cuda_is_initialised():
        return "spawn", (
            "  NOTE CUDA is already initialised in this process, so pool workers "
            "cannot be forked from it -- using spawn instead of "
            f"{wanted!r}, which costs 1-2s of pool startup."
        )
    return wanted, None


def prestart_pool() -> None:
    """Start the forkserver now, so its preload runs while the caller
    gets on with something else.

    This is where the saving actually comes from, and it is worth being
    precise about why. Workers import torch and docling *concurrently*,
    so on a host with CPUs to spare their import cost is already
    overlapped -- measured, forkserver's preload against spawn's
    per-worker imports is a wash (22.4s against 22.9s over 8 documents at
    4 workers). What is not overlapped is the preload itself: it happens
    when the pool is built, with the parent blocked on it. Started here
    instead, it runs during the ~2.5s the parent spends reading a
    646-entry bibliography, and the pool is ready 2.5s sooner (4.4s
    against 6.9s from process start to four live workers).

    `ensure_running()` returns in ~0.02s -- it launches the server and
    does not wait for its imports -- so this costs the caller nothing to
    call. It is not public API; the alternative is creating a throwaway
    Process purely for its side effect, which is worse.

    Deliberately silent about every reason it might decline: this is an
    optimisation, and a caller that gets a slower pool than it could have
    is not a caller with a problem to report.

    Declines in three cases, because starting a torch-importing process
    for a run that will not use one is pure cost:

    - not the docling backend (pdftotext gets threads, and has no use for
      torch at all);
    - [parser].workers left at its default of 1, i.e. the serial path;
    - this machine's ceiling is 1 regardless of what was asked for, which
      is `workers = "auto"` on anything up to four available CPUs. That
      case is the reason worker_ceiling() exists separately: without it,
      "auto" on a four-core laptop would launch a forkserver and import
      torch on every sync, then run serially anyway.

    What it still cannot know is how many documents need parsing -- that
    needs the bibliography this call is meant to overlap with. So a run
    with nothing to do has paid for a forkserver. That is the one case
    left, it costs a background import rather than any wall clock the
    user waits on, and closing it would mean giving up the overlap that
    is the entire point.
    """
    if config.PARSER != "docling" or config.PARSER_WORKERS == 1:
        return
    if worker_ceiling() <= 1:
        return
    if start_method()[0] != "forkserver":
        return
    from multiprocessing import forkserver

    multiprocessing.get_context("forkserver").set_forkserver_preload(preload_modules())
    try:
        forkserver.ensure_running()
    except Exception:  # noqa: BLE001 -- the pool will start one itself
        pass


# Standard-library top-level names we have actually seen shadowed by a
# path entry a dependency added to sys.path. Only `typing` so far, and
# only via OpenCV -- but the list is the mechanism, not the diagnosis.
_SHADOWED_STDLIB_NAMES = ("typing",)


def drop_stdlib_shadowing_path_entries() -> list[str]:
    """Remove sys.path entries that would shadow a standard-library
    module, and return what was removed.

    OpenCV -- pulled in transitively by docling -- appends its own package
    directory to sys.path when imported, and that directory contains a
    `typing/` package. On this process it usually loses the race against
    the standard library, so nothing is visibly wrong. In a **spawned
    worker** it does not: spawn rebuilds sys.path from the parent's, the
    cv2 entry can land ahead of the stdlib, and `import typing` then
    resolves to `cv2.typing`, which imports `cv2.dnn`, which needs
    `libGL.so.1`.

    On a host without that library -- any slim container -- the import
    fails and every worker dies before it runs a line of this project's
    code. The parse then reports every document as failed for a reason
    that names OpenCV and mentions neither PDFs nor docling.

    Worse, the failure survives its cause: `import cv2` leaves the path
    entry behind even when the import itself raises, so a host where
    OpenCV is merely *broken* poisons workers exactly like one where it
    works.

    Called before a pool is built, so children inherit a clean path.
    Deliberately conservative: an entry is only dropped if it is a
    directory *inside* site-packages that contains one of the shadowing
    names, so a site-packages directory itself is never removed even if
    something has installed a top-level `typing` backport into it.
    """
    removed = []
    for entry in list(sys.path):
        if not entry:
            continue
        parent = os.path.basename(os.path.dirname(entry.rstrip(os.sep)))
        # The entry must be a package directory *inside* an installation
        # directory. Checking only that the entry itself isn't named
        # site-packages would still drop, say, a project directory that
        # happens to contain a `typing/` package of its own.
        if parent not in {"site-packages", "dist-packages"}:
            continue
        for name in _SHADOWED_STDLIB_NAMES:
            if os.path.isfile(os.path.join(entry, name, "__init__.py")):
                sys.path.remove(entry)
                removed.append(entry)
                break
    return removed


def process_pool_context():
    """The mp context to build the docling pool on, plus any complaint
    about how it was chosen.

    Sets forkserver's preload list as a side effect, because that is the
    entire reason for preferring forkserver and the two must not drift
    apart. It has to be set before the first Process is created: the
    server is started lazily by that call and imports its preload list
    once, so a list set afterwards would be read by nothing.
    """
    drop_stdlib_shadowing_path_entries()
    method, complaint = start_method()
    ctx = multiprocessing.get_context(method)
    if method == "forkserver":
        ctx.set_forkserver_preload(preload_modules())
    return ctx, complaint


# The CUDA device this worker process was assigned, or None to leave
# Docling's own AUTO resolution alone. Process-global because it is set
# once per worker, at pool startup, and read whenever that worker builds
# a converter.
_WORKER_DEVICE = None


def worker_device() -> str | None:
    """The CUDA device this worker claimed, or None if it claimed none.

    The public read of the process-global set by init_worker, so other
    modules (src/enrich/docling_parse.py) don't reach into this one's
    internals to build their own converters.
    """
    return _WORKER_DEVICE


def _reset_worker_device() -> None:
    """Test hook -- module state otherwise leaks between tests."""
    global _WORKER_DEVICE
    _WORKER_DEVICE = None
    _reset_docling_converter()


def init_worker(counter, lock, devices) -> None:
    """Pool initialiser: claim one GPU for this worker, round-robin.

    Docling's `AcceleratorDevice.AUTO` resolves to `cuda:0` in *every*
    process, so without this, N workers all pile onto one card. Measured
    on this project's corpus before it existed: at 12 workers GPU 0 ran
    pinned at 100% while GPUs 1-3 sat at 0%, and 12 workers were no
    faster than 4.

    `devices` is usable_devices()'s list rather than a device *count*,
    so a card with no memory free is never handed out -- the round-robin
    walks the cards that can actually take a worker, and an empty list
    leaves docling's own AUTO resolution alone.

    The index comes from a shared counter rather than the worker's PID or
    position, because a ProcessPoolExecutor neither numbers its workers
    nor guarantees it starts all of them -- it creates them lazily, as
    work arrives. A counter handed out under a lock is the only thing
    that gives each *live* worker a distinct index.
    """
    global _WORKER_DEVICE
    devices = list(devices)
    if not devices:
        _WORKER_DEVICE = None
        return
    with lock:
        index = counter.value
        counter.value += 1
    _WORKER_DEVICE = f"cuda:{devices[index % len(devices)]}"


def docling_threads(workers: int) -> int:
    """Docling's per-worker thread count, divided down so that
    workers x threads still fits the machine.

    Capped at Docling's own default of 4, so the single-worker default
    resolves to exactly what Docling would have picked on its own and
    this function changes nothing until someone raises [parser].workers.

    **This matters far less than it looks.** Forcing the value to 1, 2, 4
    or 8 at 12 workers moved a full-corpus run by 1.9% -- noise (see
    docs/PERFORMANCE.md). The 8 case needed a temporary bench override,
    since the cap above puts it out of reach of any shipped
    configuration. It is kept because dividing down is still the
    correct thing to do when workers x threads would exceed the machine,
    not because it buys measurable throughput.
    """
    return max(1, min(_DOCLING_DEFAULT_THREADS, allowed_cpus() // max(workers, 1)))


class BackendUnavailable(RuntimeError):
    """config.PARSER's backend isn't usable on this host right now."""


class MissingBinary(BackendUnavailable):
    """pdftotext specifically isn't on PATH -- kept as its own subclass
    (predates the multi-backend dispatch) rather than folded into
    MissingDependency, since src/sync.py's early history and tests
    already reference it by this name."""


class MissingDependency(BackendUnavailable):
    """docling specifically isn't installed (not on PATH --
    a Python package, via pyproject.toml's "enrich" Poetry group)."""


class ExtractionError(RuntimeError):
    """The backend ran but failed on this particular PDF."""


_INSTALL_HINT = {
    "pdftotext": (
        "'pdftotext' not found on PATH. Install poppler-utils "
        "(scripts/install_full_pipeline.sh os-deps) to extract PDF text with it."
    ),
    "docling": (
        "the 'docling' package isn't usable (not installed, or a "
        "transitive dependency is broken). Run 'poetry install --with enrich' "
        "(scripts/install_full_pipeline.sh python-deps) to extract PDF text with it."
    ),
}


def _check_parser(parser: str) -> None:
    # Deliberately left to propagate uncaught out of sync.run() rather
    # than caught-and-printed like MissingBinary/MissingDependency below:
    # this is a misconfiguration (a typo'd PARSER value), not a host
    # missing an optional dependency, and sync.run() already has the same
    # shape for the other fundamental-misconfiguration case -- a missing
    # bib file raises FileNotFoundError uncaught from bib_reader.read_library(),
    # before this function's own try block even starts.
    if parser not in config.PARSER_BACKENDS:
        raise ValueError(
            f"Unknown parser backend {parser!r} (config.toml's [parser].backend, "
            f"or the PARSER env var) -- expected one of {config.PARSER_BACKENDS}."
        )


def unavailable_reason() -> str:
    """Human-readable explanation of why config.PARSER's backend isn't
    usable right now, and how to fix it. Meaningful when is_available()
    is False, and also reused as MissingDependency's message when a
    backend's import fails despite that probe passing (a broken
    transitive dependency -- see _extract_docling)."""
    _check_parser(config.PARSER)
    return _INSTALL_HINT[config.PARSER]


def is_available() -> bool:
    _check_parser(config.PARSER)
    if config.PARSER == "pdftotext":
        return shutil.which("pdftotext") is not None
    return importlib.util.find_spec(config.PARSER) is not None


def _extract_pdftotext(pdf_path: str, out_path: Path, threads: int | None = None) -> None:
    # threads is accepted and ignored: pdftotext is a single-threaded
    # external binary. The parameter exists so _EXTRACTORS stays a plain
    # uniform table rather than growing a per-backend call signature.
    #
    # Returns None -- not an empty list -- for the reason src/passages.py
    # exists: `-layout` output preserves a page's visual arrangement, so a
    # span cut from it can splice two columns together and must not be
    # quoted. The distinction matters to extract_text: None means "this
    # backend resolves no reading order", where an empty list would mean
    # "it did, and this document has no prose in it".
    try:
        subprocess.run(
            ["pdftotext", "-layout", pdf_path, str(out_path)],
            check=True,
            capture_output=True,
            text=True,
            # The one backend where a hang can genuinely be stopped:
            # this is a hard kill of an external process, not the
            # cooperative between-stages check docling offers.
            timeout=config.PARSER_DOCUMENT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        error = ExtractionError(
            f"pdftotext exceeded the {config.PARSER_DOCUMENT_TIMEOUT}s "
            "[parser].document_timeout and was killed"
        )
        # Marked, not just worded: sync reports timeouts separately from
        # the PDFs a backend genuinely cannot read, and reading that back
        # out of the message would tie the report to this string.
        error.timed_out = True
        raise error from exc
    except subprocess.CalledProcessError as exc:
        raise ExtractionError(exc.stderr or str(exc)) from exc


# One converter, reused for the whole process. Docling's
# DocumentConverter keeps its `initialized_pipelines` cache on the
# *instance*, so building one per PDF re-initialises the layout, table
# and OCR models for every single document -- measured at 16.5s of cold
# start on the documented A40 host, against a corpus of 501 PDFs.
#
# Keyed by the settings that change what a converter *is*, not merely
# memoised on "was one built already": otherwise flipping config.PARSER_OCR
# (which tests do, and a user editing config.toml mid-session would) keeps
# silently serving the converter built under the old setting.
_DOCLING_CONVERTER = None
_DOCLING_CONVERTER_KEY = None


def _reset_docling_converter() -> None:
    """Drop the cached converter. Exists for tests -- module-level state
    otherwise leaks one test's fake converter into the next."""
    global _DOCLING_CONVERTER, _DOCLING_CONVERTER_KEY
    _DOCLING_CONVERTER = None
    _DOCLING_CONVERTER_KEY = None


def _docling_converter(threads: int | None = None):
    global _DOCLING_CONVERTER, _DOCLING_CONVERTER_KEY

    key = (config.PARSER_OCR, threads, _WORKER_DEVICE, config.PARSER_DOCUMENT_TIMEOUT)
    if _DOCLING_CONVERTER is not None and _DOCLING_CONVERTER_KEY == key:
        return _DOCLING_CONVERTER

    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise MissingDependency(unavailable_reason()) from exc

    opts = PdfPipelineOptions()
    opts.do_ocr = config.PARSER_OCR
    # Docling checks this between pipeline stages, so it bounds a
    # pathologically slow document but will not interrupt a hard hang
    # inside one stage. On expiry it returns PARTIAL_SUCCESS rather than
    # raising -- which check_docling_status turns into an ExtractionError,
    # so the truncated text is never written.
    opts.document_timeout = config.PARSER_DOCUMENT_TIMEOUT
    if threads is not None or _WORKER_DEVICE is not None:
        # Only touched when a caller has worked out a thread budget or a
        # pool has claimed a GPU for this worker (i.e. when
        # [parser].workers > 1); left alone otherwise, so a default
        # single-worker run gets exactly Docling's own accelerator
        # settings and this module changes nothing about it.
        from docling.datamodel.accelerator_options import AcceleratorOptions

        kwargs = {}
        if threads is not None:
            kwargs["num_threads"] = threads
        if _WORKER_DEVICE is not None:
            kwargs["device"] = _WORKER_DEVICE
        opts.accelerator_options = AcceleratorOptions(**kwargs)
    _DOCLING_CONVERTER = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    _DOCLING_CONVERTER_KEY = key
    return _DOCLING_CONVERTER


# The wordings docling's two document_timeout paths actually produce:
# "Document processing timeout: exceeded 10.000s limit after ..." from
# the page-batch loop, and "document timeout exceeded" from the threaded
# pipeline. Only consulted when an error carries no FailureCategory --
# see _is_docling_timeout.
_DOCLING_TIMEOUT_PHRASES = ("document timeout", "document processing timeout")


def _is_docling_timeout(error, message: str) -> bool:
    """Did this ErrorItem come from `document_timeout` expiring?

    Read from docling's own `FailureCategory` rather than the wording,
    because the two code paths that can expire a document_timeout word
    themselves differently -- "Document processing timeout: exceeded
    ...s limit" from the page-batch loop, "document timeout exceeded"
    from the threaded pipeline -- and a third wording is one upstream
    release away.

    `.value`, not `str()`: FailureCategory is a str-Enum, so `str()`
    gives "FailureCategory.TIMEOUT" where the value it compares equal to
    is "timeout". Getting that wrong would silently classify every real
    timeout as an unreadable PDF, which is the failure this exists to
    prevent.

    The wording is consulted only when there is no category at all --
    what a docling build predating the field looks like -- rather than
    as a second opinion, so a categorised non-timeout error that happens
    to mention the word is not miscounted. Even then it matches the two
    phrases docling actually uses rather than the bare word "timeout",
    which a failure with nothing to do with `document_timeout` can
    legitimately contain (a model download giving up, say). Reporting
    one of those under "raise [parser].document_timeout" would send its
    reader to a setting that had no part in it.
    """
    category = getattr(error, "category", None)
    if category is not None:
        return getattr(category, "value", category) == "timeout"
    lowered = message.lower()
    return any(phrase in lowered for phrase in _DOCLING_TIMEOUT_PHRASES)


def check_docling_status(result) -> None:
    """Reject a conversion Docling only half-finished.

    `convert(raises_on_error=True)` -- the default -- raises only on
    FAILURE. A PARTIAL_SUCCESS returns quietly with a document that stops
    early: the page loop hit a bad page, or `document_timeout` expired.
    Without this check that truncated text is written to
    content/parsed/<citekey>.txt and the ledger records it as parsed, so
    every downstream consumer -- retrieval, the citation gate, provenance
    -- reasons about a source that silently ends at page k of n. On a
    citation-grounding pipeline that is worse than a visible failure.

    The parse-quality guard cannot stand in for this: it measures
    run-together words, not missing content, and its min_tokens floor
    makes it skip exactly the short documents truncation produces.
    """
    status = getattr(result, "status", None)
    name = getattr(status, "name", str(status))
    if status is None or name == "SUCCESS":
        return
    # Deduplicated and capped: docling appends one error per failed page,
    # so a timeout on a 675-page book produced 675 identical copies of
    # "document timeout exceeded" in a single line. The distinct reasons
    # are the diagnostic; the repetition is noise that buries the summary
    # line after it.
    seen, ordered, timed_out = set(), [], False
    for error in getattr(result, "errors", []):
        message = str(getattr(error, "error_message", error))
        # Classified over every error, not just the ones that survive the
        # cap below: docling appends one per failed page and the timeout
        # arrives last, so a book long enough to time out is exactly the
        # case where the deciding error is off the end of the list.
        timed_out = timed_out or _is_docling_timeout(error, message)
        if message not in seen:
            seen.add(message)
            ordered.append(message)
    errors = "; ".join(ordered[:_MAX_DOCLING_ERRORS])
    if len(ordered) > _MAX_DOCLING_ERRORS:
        errors += f"; (+{len(ordered) - _MAX_DOCLING_ERRORS} more)"
    failure = ExtractionError(
        f"docling returned {name} rather than SUCCESS -- the extracted text would "
        f"be incomplete{': ' + errors if errors else ''}"
    )
    failure.timed_out = timed_out
    raise failure


def is_cuda_oom(exc: BaseException) -> bool:
    """Does this exception mean the GPU had no memory left?

    Matched on the message rather than the type because the two shapes it
    arrives in are different classes: `torch.OutOfMemoryError` for an
    allocation torch made itself ("CUDA out of memory. Tried to allocate
    20.00 MiB"), and a bare `RuntimeError` for one the driver refused
    underneath it ("CUDA error: out of memory"), which has no dedicated
    type to catch. The run this comes from had 240 of the second and 94
    of the first, so matching only the type with a name would have missed
    the larger half.
    """
    text = str(exc)
    return "CUDA out of memory" in text or "CUDA error: out of memory" in text


def _demote_to_cpu() -> None:
    """Give up on this worker's GPU and carry on without one.

    A worker that cannot get device memory fails a document in about 19s
    where a working one takes minutes, so the pool -- which hands the
    next document to whichever worker is free first -- feeds the broken
    one preferentially. In the run this comes from, four workers of 24
    were on a card another process had filled, and they claimed and
    failed 334 of 456 documents between them. Falling back to the CPU
    turns that worker from an attractor for the whole queue back into a
    worker that is merely slower.

    No cache to clear: _docling_converter is keyed on _WORKER_DEVICE, so
    moving the device is what rebuilds it.
    """
    global _WORKER_DEVICE
    _WORKER_DEVICE = "cpu"
    print("  WARNING a parse worker ran out of GPU memory -- it has fallen back "
          "to the CPU for the rest of this run, which is slower but finishes. "
          "Another process is most likely holding the card.", file=sys.stderr)


def _extract_docling(pdf_path: str, out_path: Path, threads: int | None = None) -> list[dict]:
    """Writes Markdown, and returns the passages that Markdown can't carry.

    `result.document` carries per-item page numbers, bounding boxes and
    semantic labels -- 336 of 336 text items on a real 17-page paper, per
    docs/CITATION-PROVENANCE.md. `export_to_markdown()` keeps the reading
    order and drops the rest. One plain-text file per citekey is still the
    right shape for what the corpus layer owes its callers -- BM25 ranks
    text, not boxes -- so the structure leaves by a second door instead:
    the returned records become `content/parsed/<citekey>.passages.json`,
    rung 2 of `src/passages.py`'s ladder, and the caller (`extract_text`)
    writes them.

    Two things make that work, and both are one keyword each:

    `page_break_placeholder="\\f"` puts form feeds where the pages were, so
    this backend's output has the same shape as `pdftotext`'s and every
    consumer that splits on them -- the passage ladder's page-level rung,
    `scripts/verbatim_check.py` -- reports a real page instead of p.1.
    Docling emits a break *between* consecutive pages that carry items and
    none before the first, so splitting yields 1-based page numbers
    directly. A page carrying no items at all contributes no break and so
    shifts the pages after it; the sidecar is unaffected, because it
    records each item's own `page_no` rather than counting separators.
    `\\f` is whitespace, so BM25 tokenisation and `run_together_ratio` see
    exactly what they saw before.

    src/enrich/docling_parse.py is the other consumer of this library, and
    is still not made redundant by this one: it parses the PDF a second
    time under its own OCR and figure settings, and covers `papers/pdfs/`
    documents that have no ledger row at all.
    """
    converter = _docling_converter(threads)
    try:
        result = converter.convert(pdf_path)
        check_docling_status(result)
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- docling has no narrower
        # common exception type to catch (same reporting shape as
        # src/enrich/docling_parse.py's own parse_corpus loop).
        #
        # The converter is deliberately NOT discarded here: the failure
        # is in this one PDF, not in the models, and throwing it away
        # would charge the next document a full reload for its neighbour's
        # bad luck.
        if is_cuda_oom(exc) and _WORKER_DEVICE != "cpu":
            # Recursion is bounded by that guard: the retry runs with
            # _WORKER_DEVICE == "cpu", where this branch cannot be taken
            # again. `None` is included deliberately -- it means docling's
            # own AUTO resolution, which is cuda:0, so a serial run has
            # the same card to fall off.
            _demote_to_cpu()
            return _extract_docling(pdf_path, out_path, threads)
        error = ExtractionError(str(exc))
        if is_cuda_oom(exc):
            # Caused by the machine at this moment, not by the PDF, so it
            # must come back next run rather than being written off as a
            # document that cannot be parsed.
            error.transient = True
        raise error from exc
    out_path.write_text(
        result.document.export_to_markdown(page_break_placeholder="\f"),
        encoding="utf-8",
    )
    return passages.passage_records(result.document)


_EXTRACTORS = {
    "pdftotext": _extract_pdftotext,
    "docling": _extract_docling,
}

# A "word" for the run-together check below. Letters only: digits and
# punctuation produce long runs legitimately (DOIs, URLs, base64-ish
# identifiers, table rules) and would otherwise dominate the count.
#
# `[^\W\d_]` is "word character, but not a digit or underscore" -- i.e.
# any Unicode letter. Spelling it `[A-Za-z]` would silently split
# accented and non-Latin words ("Schroder" + "der" out of "Schröder"),
# which both hides real fusion, since a fused run containing an accent
# gets broken into short pieces, and shrinks the token count toward
# PARSE_MIN_TOKENS on non-English documents until the guard stops
# looking at them at all.
_ALPHA_RUN = re.compile(r"[^\W\d_]+")


def run_together_ratio(text: str) -> tuple[float, int]:
    """Fraction of alphabetic tokens longer than
    config.PARSE_LONG_WORD_CHARS, plus the total token count.

    A PDF text extractor decides where the spaces go by comparing glyph
    positions against a tolerance. Set that tolerance too coarse and
    adjacent words fuse -- "isaninputtooranoutputfromafunction" -- which
    is invisible in a spot check but silently wrecks retrieval, because
    src/retrieval.py tokenizes on whitespace and can no longer match a
    query term buried inside a fused run.

    Measured on this project's own corpus: pdftotext produced 9 such
    tokens out of 113,195 (0.01%) while a since-removed backend produced
    3,647 out of 87,395 (4.17%) over the same 10 PDFs -- three orders of
    magnitude apart, so any threshold between them separates a healthy
    parse from a broken one without needing to be tuned precisely.
    """
    tokens = _ALPHA_RUN.findall(text)
    if not tokens:
        return 0.0, 0
    long_tokens = sum(1 for tok in tokens if len(tok) > config.PARSE_LONG_WORD_CHARS)
    return long_tokens / len(tokens), len(tokens)


def quality_warning(text: str) -> str | None:
    """A one-line complaint about `text`, or None if it looks fine.

    Deliberately a warning rather than an error: the extraction did
    succeed, the text is usable, and a corpus of scanned or unusual
    documents could trip this legitimately. The point is that a
    systematic regression gets *reported* by sync instead of being
    noticed by eye in a retrieval snippet weeks later.
    """
    ratio, total = run_together_ratio(text)
    if total < config.PARSE_MIN_TOKENS or ratio <= config.PARSE_LONG_WORD_RATIO:
        return None
    return (
        f"{ratio:.1%} of words are longer than {config.PARSE_LONG_WORD_CHARS} "
        f"characters ({total} words checked) -- the parser is probably losing "
        f"spaces between words, which degrades retrieval"
    )


def extract_text(pdf_path: str, citekey: str, threads: int | None = None) -> Path:
    """Extract text from a PDF into content/parsed/<citekey>.txt using
    config.PARSER's backend.

    Raises MissingBinary/MissingDependency if that backend isn't usable
    on this host (probe-and-report, like every src/enrich/* stage -- see
    render_output.MissingBinary -- rather than letting the backend's own
    not-found error surface as an uncaught traceback), or ExtractionError
    if the backend runs but fails on this particular PDF.

    A backend that resolves reading order also returns passage records,
    which are written beside the text as `<citekey>.passages.json` for
    src/passages.py to quote from. The old sidecar is dropped *before*
    the parse, not replaced after it, so the three ways one can outlive
    its truth all end at "no sidecar" rather than at stale sentences
    attributed to the current PDF: the backend changed to one that
    resolves no reading order, this parse fails outright, or the same
    backend re-runs over an edited PDF.
    """
    if not is_available():
        exc_cls = MissingBinary if config.PARSER == "pdftotext" else MissingDependency
        raise exc_cls(unavailable_reason())

    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.PARSED_DIR / f"{citekey}.txt"
    passages.clear_sidecar(citekey)
    records = _EXTRACTORS[config.PARSER](pdf_path, out_path, threads)
    # `is not None`, so a backend that resolved reading order and found no
    # prose still writes an (empty) sidecar. That keeps the file's
    # presence a reliable answer to "did a reading-order backend parse
    # this?" -- which is what src/ledger.py checks before skipping a
    # document it believes is already parsed. The ladder is unaffected: it
    # declines an empty sidecar and falls to the page-level rung.
    if records is not None:
        passages.write_sidecar(citekey, records)
    return out_path


def extract_one(job: tuple[str, str, int | None]) -> tuple[str, str | None, Exception | None]:
    """Entry point for one pool worker: (pdf_path, citekey, threads) in,
    (citekey, out_path, exception) out.

    Defined at module level, and returning the exception rather than
    raising it, because both have to survive pickling across a process
    boundary. Returning it keeps the *type* -- src/sync.py distinguishes
    ExtractionError from BackendUnavailable and reports them differently,
    which a stringified error would lose.
    """
    pdf_path, citekey, threads = job
    try:
        return citekey, str(extract_text(pdf_path, citekey, threads)), None
    except (ExtractionError, BackendUnavailable) as exc:
        return citekey, None, exc
