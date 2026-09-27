"""Read-only synchronization supervised by the owning poller process."""
import logging
import os
import threading

_thread = None
_guard = threading.Lock()

def start_history_worker(on_login=None):
    """`on_login` hears every login the worker's own client spends — see history_worker."""
    global _thread
    # Cloud history is a READ: it does not follow the COMMAND qualification. An account
    # retained on the previous client — anything that is not a pure B10 account — still
    # collects it with the session it already holds: the unified mileage/daily/detail/page
    # path answers result=0 under the previous client's signature as well (measured
    # 27/09/2026 against the real cloud: 183 trips, driveReevOil included).
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
                    sync_once(on_login)
                except Exception as error:
                    logging.getLogger('mate.history').warning('History sync unavailable (%s)', type(error).__name__)
                stop.wait(300)
        _thread = threading.Thread(target=loop, name='mate-cloud-history', daemon=True)
        _thread.start()
