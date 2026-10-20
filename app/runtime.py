"""Platform-specific server startup helpers; no external dependencies."""
import os


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
