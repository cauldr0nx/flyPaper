"""Running a scan from the dashboard, under controls the browser cannot relax.

This is the one place in flypaper that starts a scan, and it is a deliberate departure from
"`fly` never initiates a request". The rest of the tool reads results an operator already
generated. A dashboard that can only replay yesterday's capture is a viewer, not an
instrument, so this launches ffuf - and everything that decides *what may be scanned* is
fixed before the browser is ever involved.

What the browser can choose: which host from the scope file, which wordlist from the
allowlisted directory, the request rate up to a ceiling, and the path template.

What the browser cannot choose, at all:

  the scope         `Scope` is loaded from a file named on the command line at startup.
                    A target whose host does not match is refused. There is no
                    same-domain inference and no wildcard default, exactly as in stage two.
  the wordlist      Only files inside the directory given at startup, by basename. Without
                    this, `--wordlist /etc/shadow` would make ffuf request each line of it
                    and the dashboard would render the result: a file disclosure with extra
                    steps.
  the rate ceiling  `MAX_RATE` is in the code. The browser can ask for less, never more.
  the shell         ffuf is executed as an argv list. No shell, ever, so a target
                    containing `;` is a target containing `;` rather than a command.

None of that makes it safe to point at a host you are not authorised against. It makes it
hard to do so *by accident*, and impossible to do so from the page alone.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from flypaper.ingest.ffuf import BASE64, ParseStats, parse_record
from flypaper.stage2.scope import Scope
from flypaper.web.replay import Recorder

__all__ = ["MAX_RATE", "ScanManager", "ScanRefused"]

#: Requests per second. The browser may ask for less and cannot ask for more; a flag is too
#: easy to get wrong and this repository has already had one target's edge protection block
#: it during a capture that was politely rate-limited and simply too long.
MAX_RATE = 40

#: A run the dashboard can hold. Beyond this the page is not the right tool - use the CLI.
MAX_REQUESTS = 20_000

#: How many frames the status endpoint hands back per poll. The page keeps what it has and
#: asks for what is new, so this only bounds one response.
PAGE = 400


class ScanRefused(ValueError):
    """The scan was not started, and the message says exactly which control refused it."""


@dataclass
class ScanState:
    status: str = "idle"  # idle | running | finished | failed | stopped
    target: str = ""
    wordlist: str = ""
    rate: int = 0
    started: float = 0.0
    finished: float = 0.0
    requests: int = 0
    surfaced: int = 0
    error: str = ""
    parse: ParseStats = field(default_factory=ParseStats)


class ScanManager:
    """One scan at a time, its frames readable while it runs."""

    def __init__(self, scope: Scope | None, wordlist_dir: Path | None) -> None:
        self.scope = scope
        self.wordlist_dir = wordlist_dir
        self.state = ScanState()
        self.recorder: Recorder | None = None
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stderr = None

    # -- what the page is allowed to ask for -------------------------------------------

    def wordlists(self) -> list[str]:
        if not self.wordlist_dir or not self.wordlist_dir.is_dir():
            return []
        return sorted(
            p.name
            for p in self.wordlist_dir.iterdir()
            if p.is_file() and not p.name.startswith(".")
        )

    def scope_hosts(self) -> list[str]:
        return sorted(self.scope.patterns) if self.scope else []

    def ready(self) -> tuple[bool, str]:
        """Whether a scan could be started at all, and why not if it could not."""
        if not shutil.which("ffuf"):
            return False, "ffuf is not on PATH"
        if not self.scope or not self.scope.patterns:
            return False, (
                "no scope file. Start the server with --scope scope.txt; there is "
                "deliberately no default and no same-domain inference."
            )
        if not self.wordlists():
            return False, (
                "no wordlists. Start the server with --wordlist-dir pointing at a directory "
                "of wordlists; the page may only choose from what is in it."
            )
        return True, ""

    # -- starting and stopping ----------------------------------------------------------

    def _resolve(self, target: str, wordlist: str, rate: int) -> tuple[str, Path, int]:
        ok, why = self.ready()
        if not ok:
            raise ScanRefused(why)
        if "FUZZ" not in target:
            raise ScanRefused("the target must contain FUZZ, e.g. https://host/FUZZ")

        # Scope is checked on the URL with the placeholder removed, because FUZZ is not a
        # host and a scope check has to see the host it will actually request.
        probe = target.replace("FUZZ", "flypaper-scope-probe")
        parts = urlsplit(probe)
        if parts.scheme not in ("http", "https"):
            raise ScanRefused(f"unsupported scheme {parts.scheme!r}; http or https only")
        if not self.scope.allows(probe):
            raise ScanRefused(
                f"{parts.netloc or target!r} is not in the scope file. The page cannot widen "
                f"scope; add the host to the file the server was started with and restart."
            )

        name = Path(wordlist).name
        if name != wordlist or not name:
            raise ScanRefused("wordlist must be a plain filename from the allowed directory")
        path = (self.wordlist_dir / name).resolve()
        if not str(path).startswith(str(self.wordlist_dir.resolve())) or not path.is_file():
            raise ScanRefused(f"{wordlist!r} is not in the allowed wordlist directory")

        return target, path, max(1, min(int(rate), MAX_RATE))

    def start(self, target: str, wordlist: str, rate: int = 20) -> None:
        with self._lock:
            if self.state.status == "running":
                raise ScanRefused("a scan is already running; stop it first")
            target, path, rate = self._resolve(target, wordlist, rate)

            self.recorder = Recorder(label=urlsplit(target.replace("FUZZ", "")).netloc)
            self.state = ScanState(
                status="running",
                target=target,
                wordlist=path.name,
                rate=rate,
                started=time.time(),
            )
            # argv, never a shell: a target containing a semicolon is a target.
            argv = [
                "ffuf",
                "-mc",
                "all",  # the noise is the baseline; never pre-filter it
                "-json",
                "-u",
                target,
                "-w",
                str(path),
                "-rate",
                str(rate),
                "-t",
                str(min(rate, 20)),
                "-noninteractive",
            ]
            # stderr goes to a file, not a pipe. ffuf writes progress there even with
            # -noninteractive, and a pipe nobody drains fills its 64 kB buffer and blocks
            # the process forever - which looks exactly like a scan that never finishes.
            # A short run does not fill it, so this only ever appears on a real one.
            self._stderr = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed in _finish
                prefix="flypaper-ffuf-", suffix=".log", delete=False
            )
            self._proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=self._stderr, text=False
            )
            self._thread = threading.Thread(target=self._read, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self.state.status == "running":
            self.state.status = "stopped"
            self.state.finished = time.time()

    def _read(self) -> None:
        """Parse ffuf's stdout as it arrives and score each response immediately."""
        proc = self._proc
        assert proc and proc.stdout
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    self.state.parse.malformed += 1
                    continue
                # ffuf's stdout stream base64s its input map; the file writer does not. This
                # is decided per source rather than sniffed per value, because sniffing
                # corrupts real words that happen to look like base64.
                result = parse_record(record, encoding=BASE64, stats=self.state.parse)
                frame = self.recorder.feed(result)
                self.recorder.keep(frame)
                self.state.requests += 1
                if frame["shown"]:
                    self.state.surfaced += 1
                if self.state.requests >= MAX_REQUESTS:
                    self.state.error = f"stopped at the {MAX_REQUESTS} request ceiling"
                    break
        except Exception as exc:  # noqa: BLE001 - a reader thread must not die silently
            self.state.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._finish(proc)

    def _finish(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
        code = proc.wait()
        stderr = ""
        handle, self._stderr = self._stderr, None
        if handle is not None:
            try:
                handle.close()
                stderr = Path(handle.name).read_text("utf-8", "replace")
                Path(handle.name).unlink(missing_ok=True)
            except OSError:
                pass
        self.state.finished = time.time()
        if self.state.status == "stopped":
            return
        if code not in (0, -15) and not self.state.error:
            self.state.error = (stderr.strip().splitlines() or ["ffuf failed"])[-1][:400]
        self.state.status = "failed" if self.state.error else "finished"

    # -- what the page reads -------------------------------------------------------------

    def status(self, since: int = 0) -> dict:
        """State, plus the frames after `since`. The page asks only for what it lacks."""
        rec = self.recorder
        frames = rec.frames[since : since + PAGE] if rec else []
        state = self.state
        out = {
            "status": state.status,
            "target": state.target,
            "wordlist": state.wordlist,
            "rate": state.rate,
            "requests": state.requests,
            "surfaced": state.surfaced,
            "malformed": state.parse.malformed,
            "elapsed": round((state.finished or time.time()) - state.started, 1)
            if state.started
            else 0.0,
            "error": state.error,
            "frames": frames,
            "next": since + len(frames),
            "complete": state.status in ("finished", "failed", "stopped"),
        }
        if rec and rec.frames:
            out["meta"] = {
                "channel_set": rec.ranker.channel_set,
                "projection": rec.ranker.projection,
                "n_kc": int(rec.ranker.flyhash.n_kc),
                "n_active": int(rec.ranker.flyhash.n_active),
                "saturation": round(rec.ranker.saturation, 5),
            }
        return out
