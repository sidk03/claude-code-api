import asyncio
import os
import json
from pathlib import Path
from logging.logging_config import config_logging
import logging
from enum import StrEnum
from typing import Optional

config_logging("test_logs")
logger = logging.getLogger(__name__)


class FilePermissions(StrEnum):
    READ_ONLY = "read_only"
    FULL_ACCESS = "full_access"


class ClaudeRunner:
    __slots__ = ("_permissions", "_retries", "_sem")

    def __init__(
        self,
        permissions: FilePermissions,
        retries: int = 2,
        run_limit: Optional[int] = None,
    ):
        self._permissions = permissions
        self._retries = retries
        self._sem = Optional[asyncio.Semaphore] = (
            asyncio.BoundedSemaphore(run_limit) if run_limit else None
        )

    def _get_allowed_tools(self) -> list[str]:
        return (
            [
                "Read",
                "LS",
                "Glob",
                "Grep",
                "WebFetch",
                "WebSearch",
                "Bash",
                "TodoRead",
                "Agent",
            ]
            if self._permissions == FilePermissions.READ_ONLY
            else [
                "Read",
                "LS",
                "Glob",
                "Grep",
                "Write",
                "Edit",
                "MultiEdit",
                "Bash",
                "NotebookRead",
                "NotebookEdit",
                "TodoRead",
                "TodoWrite",
                "WebFetch",
                "WebSearch",
                "Agent",
            ]
        )

    async def _stream_stdout_handler(stream: asyncio.StreamReader):
       
        async for line in stream:
            line = line.decode("utf-8").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                msg_type = data.get("type")
                session_id = data.get("session_id")

                if msg_type in ["system", "assistant"]:
                    content = data.get("content", str(data))
                    logger.info(content, {"claude_session":session_id})
                elif msg_type == "result":
                    result = data.get("result", str(data))
                    logger.info(f"Final Message Recieved : {result}", {"claude_session":session_id})
                elif msg_type == "user":
                    pass
                else:
                    logger.debug(f"[OTHER] Received unhandled message type: {line}", {"claude_session":session_id})
            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON line from stdout: {line}")
            except Exception as e:
                logger.error(f"Error processing stream line: {line}. Error: {e}")


                

       
