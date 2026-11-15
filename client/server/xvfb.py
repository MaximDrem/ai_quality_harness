"""Виртуальный X-дисплей для render-verify скиллов.

Скиллы (presentation) в verify-режиме перезапускают реальное Electron-
приложение, чтобы собрать `<slug>-verify/`. Electron оффскрин без X-сервера не
стартует (ozone), поэтому headless-инстанс поднимает Xvfb сам.
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Optional


class XvfbDisplay:
    """Управление Xvfb: поднять при необходимости и погасить при остановке."""

    def __init__(self, display: str, enabled: bool = True, screen: str = "1280x900x24"):
        self.display = display
        self.enabled = enabled
        self.screen = screen
        self.process: Optional[subprocess.Popen] = None

    def resolve_display(self) -> Optional[str]:
        """DISPLAY для движка: внешний, если задан, иначе виртуальный."""
        if "DISPLAY" in os.environ:
            return os.environ["DISPLAY"]
        return self.display if self.enabled else None

    def start(self) -> Optional[str]:
        """Поднять Xvfb, если DISPLAY не задан и Xvfb включён.

        Не бросает исключение, если Xvfb недоступен: render-verify просто
        пропускается (папка `-verify/` не создаётся), это не ошибка кейса.
        """
        display = self.resolve_display()
        if display is None or "DISPLAY" in os.environ:
            return display
        if self.process is not None and self.process.poll() is None:
            return display
        try:
            self.process = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", self.screen, "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            # Дать X-серверу время подняться.
            time.sleep(1.0)
        except FileNotFoundError:
            self.process = None
        return display

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = 10.0) -> None:
        proc, self.process = self.process, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


__all__ = ["XvfbDisplay"]