"""Die letzten Logzeilen im Arbeitsspeicher — für die Seite „System-Log“.

Kein Ersatz für `docker compose logs`: nach einem Neustart ist der Puffer leer.
"""
import logging
import time
from collections import deque

STARTED = time.time()
MAX_LINES = 2000
FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


class BufferHandler(logging.Handler):
    def __init__(self, maxlen: int = MAX_LINES):
        super().__init__()
        self.lines = deque(maxlen=maxlen)
        self.setFormatter(logging.Formatter(FORMAT))

    def emit(self, record):
        if record.name == 'aiohttp.access':   # jede Seitenabfrage — würde den Puffer fluten
            return
        try:
            self.lines.append((record.levelno, self.format(record)))
        except Exception:   # Logging darf nie den Betrieb stören
            self.handleError(record)


def install() -> BufferHandler:
    """Hängt den Puffer einmal an den Root-Logger und gibt ihn zurück."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, BufferHandler):
            return h
    handler = BufferHandler()
    root.addHandler(handler)
    return handler
