"""Allow only one running copy; a second launch asks the first to show itself."""
from __future__ import annotations

import getpass
import hashlib
import logging
import re
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, QLockFile, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger("live_assistant.instance")


def instance_key() -> str:
    try:
        user = getpass.getuser() or "user"
    except Exception:
        user = "user"
    return "LocalAssistant-" + re.sub(r"[^A-Za-z0-9_]", "_", user)


def _lock_path(key: str) -> Path:
    """Return a short, per-user lock filename without exposing the user name."""
    digest = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"localassistant-{digest}.lock"


class SingleInstance(QObject):
    """Cross-process single-instance guard.

    QLockFile is the authoritative exclusivity mechanism. QLocalServer is only
    the activation channel. Keeping these responsibilities separate avoids
    relying on platform-specific QLocalServer listen semantics (notably named
    pipes on Windows) to enforce exclusivity.
    """

    activated = Signal()  # another launch asked us to show the window

    def __init__(self, key: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key or instance_key()
        self._legacy_key = self._key.replace("LocalAssistant-", "LiveAssistant-", 1)
        self._server: QLocalServer | None = None
        self._clients: list[QLocalSocket] = []
        self._client_buffers: dict[QLocalSocket, bytearray] = {}
        self._existing_instance_notified = False
        self._lock = QLockFile(str(_lock_path(self._key)))
        # A long-lived application must not have its live lock considered stale
        # merely because it has been running for more than the default 30 s.
        # QLockFile still detects a dead owning process and removes stale locks.
        self._lock.setStaleLockTime(0)

    @property
    def existing_instance_notified(self) -> bool:
        return self._existing_instance_notified

    def notify_existing(self, timeout_ms: int = 500) -> bool:
        """If another instance is running, ask it to show itself."""
        for key in (self._key, self._legacy_key):
            socket = QLocalSocket()
            socket.connectToServer(key)
            if not socket.waitForConnected(timeout_ms):
                socket.abort()
                continue
            socket.write(b"show\n")
            socket.flush()
            socket.waitForBytesWritten(timeout_ms)
            socket.disconnectFromServer()
            return True
        return False

    def _release_lock(self) -> None:
        if self._lock.isLocked():
            self._lock.unlock()

    def listen(self) -> bool:
        """Acquire the exclusive process lock, then create the activation server."""
        if self._server is not None and self._server.isListening():
            return True

        if not self._lock.tryLock(0):
            # The owner may have acquired the lock just before it creates its
            # local socket. Give that short startup window time to complete.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if self.notify_existing(timeout_ms=150):
                    self._existing_instance_notified = True
                    logger.info("Другой экземпляр LocalAssistant уже работает.")
                    return False
                time.sleep(0.05)

            try:
                pid, host, app = self._lock.getLockInfo()
                owner = f"PID={pid}, host={host}, app={app}"
            except Exception:
                owner = "информация о владельце недоступна"
            logger.warning("Экземпляр LocalAssistant уже удерживает блокировку (%s).", owner)
            return False

        # A previous LiveAssistant build does not know about QLockFile. Check
        # its legacy socket before opening our new one.
        if self.notify_existing(timeout_ms=250):
            self._existing_instance_notified = True
            self._release_lock()
            logger.info("Обнаружен работающий экземпляр предыдущей версии.")
            return False

        server = QLocalServer(self)
        if server.listen(self._key):
            server.newConnection.connect(self._on_connection)
            self._server = server
            return True

        # Never detach an endpoint that may belong to a live process. If a
        # server is present but cannot be contacted, fail closed and explain
        # the issue rather than risk allowing two application instances.
        if server.serverError() == QLocalServer.ServerError.AddressInUseError:
            if self.notify_existing(timeout_ms=1000):
                self._existing_instance_notified = True
                logger.info("Другой экземпляр LocalAssistant уже работает.")
            else:
                logger.error(
                    "Имя локального IPC-сервера занято, но владелец не отвечает; "
                    "не удаляю endpoint автоматически."
                )
        else:
            logger.error("Не удалось открыть IPC-сервер: %s", server.errorString())

        server.deleteLater()
        self._release_lock()
        return False

    def close(self) -> None:
        """Release IPC resources and the process lock during normal shutdown."""
        for socket in list(self._clients):
            socket.disconnectFromServer()
            socket.deleteLater()
        self._clients.clear()
        self._client_buffers.clear()
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self._key)
            self._server.deleteLater()
            self._server = None
        self._release_lock()

    def _on_connection(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                break
            self._clients.append(socket)
            self._client_buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._on_ready(s))
            socket.disconnected.connect(lambda s=socket: self._drop(s))
            # The peer may have written before newConnection was dispatched.
            if socket.bytesAvailable():
                self._on_ready(socket)

    def _on_ready(self, socket: QLocalSocket) -> None:
        buffer = self._client_buffers.setdefault(socket, bytearray())
        buffer.extend(bytes(socket.readAll()))
        while b"\n" in buffer:
            line, _, remainder = buffer.partition(b"\n")
            buffer[:] = remainder
            if line.strip() == b"show":
                self.activated.emit()

    def _drop(self, socket: QLocalSocket) -> None:
        if socket in self._clients:
            self._clients.remove(socket)
        self._client_buffers.pop(socket, None)
        socket.deleteLater()
