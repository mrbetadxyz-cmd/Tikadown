import asyncio
import os
import logging
from telethon import TelegramClient, helpers
from telethon.sessions import StringSession
from telethon.network import MTProtoSender
from telethon.tl import functions
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions.upload import SaveBigFilePartRequest
from telethon.tl.types import InputFileBig
from config import settings

logger = logging.getLogger(__name__)

import socket

def has_ipv6_connection() -> bool:
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(("2001:4860:4860::8888", 53))
        s.close()
        return True
    except Exception:
        return False

use_ipv6 = has_ipv6_connection()
logger.info(f"Dynamic IPv6 connectivity check: {'Enabled' if use_ipv6 else 'Disabled (Using IPv4)'}")

session_storage = StringSession(settings.telethon_session) if settings.telethon_session else 'userbot'

userbot_client = TelegramClient(
    session_storage,
    settings.userbot_api_id,
    settings.userbot_api_hash,
    use_ipv6=use_ipv6
)

async def start_userbot():
    """
    Start userbot client session (interactive on first start).
    """
    logger.info("Starting Telethon Userbot helper client...")
    await userbot_client.connect()
    
    # Check if authorized, if not start it interactively
    if not await userbot_client.is_user_authorized():
        logger.info("Userbot is not authorized. Please authorize via terminal.")
        await userbot_client.start()
    else:
        logger.info("Userbot helper client authorized successfully!")

async def _parallel_upload_big_file(
    client: TelegramClient,
    filepath: str,
    progress_callback=None,
    workers: int = 6,
) -> InputFileBig:
    """
    Upload a >= 10MB file across multiple concurrent MTProto sender connections
    with 512KB parts and bounded memory usage.
    """
    file_size = os.path.getsize(filepath)
    part_size = 512 * 1024
    part_count = (file_size + part_size - 1) // part_size
    workers = max(1, min(workers, part_count))

    file_id = helpers.generate_random_long()
    dc_id = client.session.dc_id
    senders = []
    chunk_queue: asyncio.Queue = asyncio.Queue(maxsize=workers * 2)
    uploaded_bytes = 0

    try:
        for _ in range(workers):
            sender = MTProtoSender(client.session.auth_key, loggers=client._log)
            senders.append(sender)
            await sender.connect(client._connection(
                client.session.server_address,
                client.session.port,
                client.session.dc_id,
                loggers=client._log,
                proxy=client._proxy,
                local_addr=client._local_addr,
            ))
            client._init_request.query = functions.help.GetConfigRequest()
            await sender.send(functions.InvokeWithLayerRequest(
                LAYER,
                functions.InvokeWithoutUpdatesRequest(client._init_request),
            ))

        async def _reader():
            try:
                with open(filepath, "rb") as f:
                    for part_idx in range(part_count):
                        data = await asyncio.to_thread(f.read, part_size)
                        if not data:
                            break
                        await chunk_queue.put((part_idx, data))
            finally:
                for _ in range(workers):
                    await chunk_queue.put(None)

        async def _worker(sender):
            nonlocal uploaded_bytes
            while True:
                item = await chunk_queue.get()
                if item is None:
                    break
                part_idx, data = item
                await sender.send(SaveBigFilePartRequest(
                    file_id=file_id,
                    file_part=part_idx,
                    file_total_parts=part_count,
                    bytes=data,
                ))
                uploaded_bytes += len(data)
                if progress_callback:
                    await progress_callback(min(uploaded_bytes, file_size), file_size)

        tasks = [asyncio.create_task(_reader())] + [asyncio.create_task(_worker(s)) for s in senders]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        return InputFileBig(
            id=file_id,
            parts=part_count,
            name=os.path.basename(filepath),
        )
    finally:
        if senders:
            await asyncio.gather(*(s.disconnect() for s in senders), return_exceptions=True)


async def upload_large_file(
    filepath: str, 
    progress_callback=None,
    duration=None,
    width=None,
    height=None,
    thumb_path=None
) -> int:
    """
    Upload a large file to the configured channel with metadata.
    Uses concurrent MTProto workers for files >= 10MB and falls back cleanly to standard Telethon.
    Returns the message ID of the uploaded file.
    """
    async def event_callback(current, total):
        if progress_callback:
            percent = (current / total * 100) if total > 0 else 0
            try:
                await progress_callback(percent, current, total, 0)
            except Exception as e:
                logger.error(f"Error in userbot progress callback: {e}")

    # Build video attributes if metadata is available
    from telethon.tl.types import DocumentAttributeVideo
    attributes = []
    if duration or width or height:
        attributes.append(DocumentAttributeVideo(
            duration=int(duration) if duration else 0,
            w=int(width) if width else 0,
            h=int(height) if height else 0,
            supports_streaming=True
        ))

    file_size = os.path.getsize(filepath)
    logger.info(f"Userbot helper uploading {filepath} ({file_size} bytes) to channel {settings.userbot_channel_id} with thumb={thumb_path}...")

    upload_target = filepath
    used_parallel = False
    if file_size >= 10 * 1024 * 1024:
        worker_count = 6 if file_size >= 50 * 1024 * 1024 else 4
        try:
            upload_target = await _parallel_upload_big_file(
                userbot_client,
                filepath,
                progress_callback=event_callback if progress_callback else None,
                workers=worker_count,
            )
            used_parallel = True
        except asyncio.CancelledError:
            raise
        except Exception as par_err:
            logger.warning(f"Parallel userbot upload failed ({par_err}), falling back to standard upload...")
            upload_target = filepath

    message = await userbot_client.send_file(
        entity=settings.userbot_channel_id,
        file=upload_target,
        thumb=thumb_path,
        attributes=attributes if attributes else None,
        supports_streaming=True,
        part_size_kb=512,
        progress_callback=(event_callback if (progress_callback and not used_parallel) else None)
    )
    logger.info(f"Userbot helper uploaded file successfully. Message ID: {message.id}")
    return message.id
