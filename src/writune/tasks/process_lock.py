"""OS-owned worker lock, released by the kernel even after a hard crash.

Leases diagnose abandoned work. The process lock prevents a second set of
provider/model semaphores while a paused worker still owns network sockets.
"""
import os
from pathlib import Path

class ProcessLock:
    def __init__(self,path):
        self.path=Path(path)
        self.stream=None

    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        stream=self.path.open('a+b')
        try:
            if os.name=='nt':
                import msvcrt
                if self.path.stat().st_size==0:
                    stream.write(b'0');stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream=stream
        return True

    def release(self):
        if self.stream is None:
            return
        try:
            if os.name=='nt':
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream=None
