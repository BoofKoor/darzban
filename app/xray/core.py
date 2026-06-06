from __future__ import annotations

import atexit
import re
import subprocess
import threading
from collections import deque
from contextlib import contextmanager
from functools import cached_property
from typing import TYPE_CHECKING

from app import logger
from config import (
    DEBUG,
    XRAY_EXECUTABLE_PATH,
    XRAY_VALIDATE_BEFORE_APPLY,
    XRAY_VALIDATE_TIMEOUT,
)

if TYPE_CHECKING:
    from app.xray.config import XRayConfig


class XRayConfigError(ValueError):
    """Raised when the bundled Xray binary rejects a config via `run -test`.

    Subclasses ValueError so existing `except ValueError` config-handling
    paths keep treating a bad config as a 4xx-class problem. Carries the
    binary's stderr so the API can surface the real reason to the operator.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.stderr = message


def get_mldsa65(seed: str, executable_path: str = XRAY_EXECUTABLE_PATH):
    """Derive REALITY mldsa65 verify key from the operator-supplied seed.

    Module-level (not a method on XRayCore) so the resolver can import it
    directly via `from app.xray.core import get_mldsa65` at module top
    without routing through `app.xray.__getattr__` — which would
    recursively re-enter `_initialize()` during XRayConfig construction.
    """
    cmd = [executable_path, "mldsa65", "-i", seed]
    output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8")
    # `xray mldsa65 -i <seed>` output (v26.5.9):
    #   Seed:   <base64.RawURLEncoding>
    #   Verify: <base64.RawURLEncoding, ~2400 chars>
    seed_m = re.search(r"^Seed:\s*(\S+)", output, re.MULTILINE)
    verify_m = re.search(r"^Verify:\s*(\S+)", output, re.MULTILINE)
    if seed_m and verify_m:
        return {
            "seed": seed_m.group(1),
            "verify": verify_m.group(1),
        }
    return None


class XRayCore:
    def __init__(self,
                 executable_path: str = "/usr/bin/xray",
                 assets_path: str = "/usr/share/xray"):
        self.executable_path = executable_path
        self.assets_path = assets_path

        # `version` is a cached_property — the subprocess that asks
        # Xray for its version is deferred until the first read.
        # This keeps XRayCore construction side-effect-free, which is
        # what lets `import app.xray` happen without an Xray binary.
        self.process = None
        self.restarting = False

        self._logs_buffer = deque(maxlen=100)
        self._temp_log_buffers = {}
        self._on_start_funcs = []
        self._on_stop_funcs = []
        self._env = {
            "XRAY_LOCATION_ASSET": assets_path
        }

        atexit.register(lambda: self.stop() if self.started else None)

    @cached_property
    def version(self):
        return self.get_version()

    def get_version(self):
        cmd = [self.executable_path, "version"]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode('utf-8')
        m = re.match(r'^Xray (\d+\.\d+\.\d+)', output)
        if m:
            return m.groups()[0]

    def get_x25519(self, private_key: str = None):
        cmd = [self.executable_path, "x25519"]
        if private_key:
            cmd.extend(['-i', private_key])
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode('utf-8')
        # Upstream renamed the labels at v25.3.6. Confirmed against the
        # v26.5.9 binary (`xray x25519`):
        #   pre-25.3.6: "Private key: …\nPublic key: …"
        #   v26.5.9   : "PrivateKey: …\nPassword (PublicKey): …\nHash32: …"
        # The public key is the "Password" line (label literally reads
        # "Password (PublicKey)"); "Hash32" is unrelated and ignored.
        # `[^:]*` swallows any "(PublicKey)"-style parenthetical between
        # the keyword and the colon so both label spellings parse.
        priv = re.search(r'^Private\s?[Kk]ey[^:]*:\s*(\S+)', output, re.MULTILINE)
        pub = re.search(r'^(?:Password|Public\s?[Kk]ey)[^:]*:\s*(\S+)', output, re.MULTILINE)
        if priv and pub:
            return {
                "private_key": priv.group(1),
                "public_key": pub.group(1),
            }

    def __capture_process_logs(self):
        def capture_and_debug_log():
            while self.process:
                output = self.process.stdout.readline()
                if output:
                    output = output.strip()
                    self._logs_buffer.append(output)
                    for buf in list(self._temp_log_buffers.values()):
                        buf.append(output)
                    logger.debug(output)

                elif not self.process or self.process.poll() is not None:
                    break

        def capture_only():
            while self.process:
                output = self.process.stdout.readline()
                if output:
                    output = output.strip()
                    self._logs_buffer.append(output)
                    for buf in list(self._temp_log_buffers.values()):
                        buf.append(output)

                elif not self.process or self.process.poll() is not None:
                    break

        if DEBUG:
            threading.Thread(target=capture_and_debug_log).start()
        else:
            threading.Thread(target=capture_only).start()

    @contextmanager
    def get_logs(self):
        buf = deque(self._logs_buffer, maxlen=100)
        buf_id = id(buf)
        try:
            self._temp_log_buffers[buf_id] = buf
            yield buf
        finally:
            del self._temp_log_buffers[buf_id]
            del buf

    @property
    def started(self):
        if not self.process:
            return False

        if self.process.poll() is None:
            return True

        return False

    def start(self, config: XRayConfig):
        if self.started is True:
            raise RuntimeError("Xray is started already")

        if config.get('log', {}).get('logLevel') in ('none', 'error'):
            config['log']['logLevel'] = 'warning'

        cmd = [
            self.executable_path,
            "run",
            '-config',
            'stdin:'
        ]
        self.process = subprocess.Popen(
            cmd,
            env=self._env,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            universal_newlines=True
        )
        self.process.stdin.write(config.to_json())
        self.process.stdin.flush()
        self.process.stdin.close()
        logger.warning(f"Xray core {self.version} started")

        self.__capture_process_logs()

        # execute on start functions
        for func in self._on_start_funcs:
            threading.Thread(target=func).start()

    def stop(self):
        if not self.started:
            return

        self.process.terminate()
        self.process = None
        logger.warning("Xray core stopped")

        # execute on stop functions
        for func in self._on_stop_funcs:
            threading.Thread(target=func).start()

    def restart(self, config: XRayConfig):
        if self.restarting is True:
            return

        try:
            self.restarting = True
            logger.warning("Restarting Xray core...")
            self.stop()
            self.start(config)
        finally:
            self.restarting = False

    def test_config(self, config: XRayConfig):
        """Validate `config` with `xray run -test` before it is applied.

        Mirrors `start()` exactly — same cmd shape, same env, config fed on
        stdin — so a passing test faithfully predicts what `start()`/`restart()`
        would run. Pass the already-resolved config (i.e. the output of
        `include_db_users()`), which is what actually gets started.

        Raises `XRayConfigError` (carrying Xray's stderr) when the binary
        rejects the config. Soft-passes — never raises — when validation
        cannot be performed (binary missing, times out) or is disabled via
        `XRAY_VALIDATE_BEFORE_APPLY`, so this gate can only ever *prevent* a
        known-bad apply, never make the panel less available than before.
        """
        if not XRAY_VALIDATE_BEFORE_APPLY:
            return

        cmd = [self.executable_path, "run", "-test", "-config", "stdin:"]
        try:
            result = subprocess.run(
                cmd,
                input=config.to_json(),
                env=self._env,
                capture_output=True,
                text=True,
                timeout=XRAY_VALIDATE_TIMEOUT,
            )
        except FileNotFoundError:
            logger.warning(
                f"Xray binary not found at {self.executable_path}; "
                "skipping pre-apply config validation"
            )
            return
        except subprocess.TimeoutExpired:
            logger.warning(
                f"Xray config validation timed out after {XRAY_VALIDATE_TIMEOUT}s; "
                "applying without validation"
            )
            return

        if result.returncode != 0:
            raise XRayConfigError((result.stderr or result.stdout or "").strip())

    def on_start(self, func: callable):
        self._on_start_funcs.append(func)
        return func

    def on_stop(self, func: callable):
        self._on_stop_funcs.append(func)
        return func
