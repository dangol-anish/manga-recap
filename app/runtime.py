"""Platform-specific server startup helpers; no external dependencies."""
import os
import sys
import subprocess
import warnings
from contextlib import contextmanager


@contextmanager
def keep_awake():
    """Prevent idle system sleep during detection; released on this thread/exit."""
    awake = None
    execution_state = None
    if sys.platform == 'darwin':
        awake = subprocess.Popen(['caffeinate', '-i', '-w', str(os.getpid())])
    elif sys.platform == 'win32':
        import ctypes
        execution_state = ctypes.windll.kernel32.SetThreadExecutionState
        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED, without keeping the screen on.
        if not execution_state(0x80000001):
            warnings.warn('Windows could not prevent idle sleep. Keep the PC awake while detecting panels.', RuntimeWarning)
    try:
        yield
    finally:
        if execution_state is not None:
            execution_state(0x80000000)
        if awake is not None:
            awake.terminate()
            awake.wait()


def acquire_server_lock(path):
    stream = open(path, 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            # Windows byte locks need a real byte and a stable file offset.
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise RuntimeError('Another manga-recap server is running, or its lock cannot be acquired.') from exc
    # Closing the handle releases the OS lock on either platform.
    return stream


def open_system_folder(folder):
    import subprocess
    import sys
    if sys.platform == 'darwin':
        subprocess.run(['open', str(folder)], check=True, timeout=10)
    elif sys.platform == 'win32':
        os.startfile(str(folder))
    else:
        raise ValueError('Opening folders is supported on macOS and Windows.')
