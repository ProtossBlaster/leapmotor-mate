"""Read-only synchronization supervised by the owning poller process."""
import logging
import os
import threading

_thread = None
_guard = threading.Lock()

def start_history_worker():
    global _thread
    if os.environ.get('MATE_DEMO', '').lower() in ('1', 'true'):
        return
    with _guard:
        if _thread is not None and _thread.is_alive():
            return
        from history_worker import sync_once
        def loop():
            stop = threading.Event()
            while True:
                try:
                    sync_once()
                except Exception as error:
                    logging.getLogger('mate.history').warning('History sync unavailable (%s)', type(error).__name__)
                stop.wait(300)
        _thread = threading.Thread(target=loop, name='mate-cloud-history', daemon=True)
        _thread.start()
