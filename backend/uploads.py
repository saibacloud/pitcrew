# Upload helpers — bounded reads so an oversized file never sits whole in memory

import os

from fastapi import HTTPException, UploadFile

_CHUNK = 1024 * 1024


def _too_large(max_bytes: int) -> HTTPException:
    return HTTPException(400, f'File too large (max {max_bytes // (1024 * 1024)}MB)')


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload into memory, stopping as soon as it passes max_bytes."""
    buf = bytearray()
    while chunk := await file.read(_CHUNK):
        buf += chunk
        if len(buf) > max_bytes:
            raise _too_large(max_bytes)
    return bytes(buf)


async def save_upload(file: UploadFile, dest: str, max_bytes: int) -> None:
    """Stream an upload to dest; a partial file is removed if it is too large or fails."""
    size = 0
    try:
        with open(dest, 'wb') as out:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise _too_large(max_bytes)
                out.write(chunk)
    except BaseException:
        if os.path.exists(dest):
            os.remove(dest)
        raise
