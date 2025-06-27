import asyncio
import uuid
import os
import json
import shutil
from common.logging_config import config_logging
import logging
from enum import StrEnum
from typing import Optional

config_logging("test_logs.jsonl")
logger = logging.getLogger(__name__)
# logger = logging.LoggerAdapter(logger, extra={"session_id":10})


class ClaudeProcessError(Exception):
    def __init__(self, message, result_data=None):
        super().__init__(message)
        self.result_data = result_data


class CLAUDE_CODE_MODELS(StrEnum):
    CLAUDE_SONNET_4 = "claude-sonnet-4-20250514"
    CLAUDE_OPUS_4 = "claude-opus-4-20250514"


class FilePermissions(StrEnum):
    READ_ONLY = "read_only"
    FULL_ACCESS = "full_access"


class ClaudeCodeRunner:
    __slots__ = ("_permissions", "_retries")

    def __init__(
        self,
        permissions: FilePermissions,
        retries: int = 2,
    ):
        self._permissions = permissions
        self._retries = max(0, retries)

    def _get_allowed_tools(self) -> list[str]:
        if self._permissions == FilePermissions.READ_ONLY:
            return [
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

        elif self._permissions == FilePermissions.FULL_ACCESS:
            return [
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
        else:
            return []

    async def _stream_stdout_handler(
        self, stream: asyncio.StreamReader, run_session_id: str
    ) -> Optional[dict]:
        async for line in stream:
            line = line.decode("utf-8").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                msg_type = data.get("type")
                claude_session_id = data.get("session_id")

                if msg_type == "system":
                    content = str(data)
                    logger.info(
                        f"[{msg_type.upper()} : {content}]",
                        extra={
                            "claude_session_id": claude_session_id,
                            "run_session_id": run_session_id,
                            "run_completed": False,
                        },
                    )
                elif msg_type == "assistant":
                    content = data.get("message", {}).get("content", str(data))
                    logger.info(
                        f"[{msg_type.upper()}] : {content}]",
                        extra={
                            "claude_session_id": claude_session_id,
                            "run_session_id": run_session_id,
                            "run_completed": False,
                        },
                    )
                elif msg_type == "result":
                    result = data.get("result", str(data))
                    logger.info(
                        f"[Final Message Recieved : {result}]",
                        extra={
                            "claude_session_id": claude_session_id,
                            "run_session_id": run_session_id,
                            "run_completed": True,
                        },
                    )
                    return data
                elif msg_type == "user":
                    pass
                else:
                    logger.debug(
                        f"[OTHER] Received unhandled message type: {line}",
                        extra={
                            "claude_session_id": claude_session_id,
                            "run_session_id": run_session_id,
                            "run_completed": False,
                        },
                    )
            except json.JSONDecodeError:
                logger.warning(
                    f"Received non-JSON line from stdout: {line}",
                    extra={"run_session_id": run_session_id, "run_completed": False},
                )
            except Exception as e:
                logger.error(
                    f"Error processing stream line: {line}. Error: {e}",
                    extra={"run_session_id": run_session_id, "run_completed": False},
                )

        return None

    async def _stream_stderr_handler(
        self, stream: asyncio.StreamReader, run_session_id: str
    ) -> str:
        async for line in stream:
            line = line.decode("utf-8").strip()
            if not line:
                continue
            logger.error(
                line, extra={"run_session_id": run_session_id, "run_completed": False}
            )

    async def _run_claude_instance(
        self,
        prompt: str,
        directory: str,
        run_session_id: str,
        model: CLAUDE_CODE_MODELS = CLAUDE_CODE_MODELS.CLAUDE_SONNET_4,
        continue_conversation: bool = False,
    ) -> str:
        async def _run_and_stream(
            cmd_args: list[str],
        ) -> tuple[int, str, Optional[dict]]:
            logger.debug(
                f"Executing command: {' '.join(cmd_args)}",
                extra={"run_session_id": run_session_id, "run_completed": False},
            )
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                cwd=directory,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
            stdout_task = asyncio.create_task(
                self._stream_stdout_handler(process.stdout, run_session_id)
            )
            stderr_task = asyncio.create_task(
                self._stream_stderr_handler(process.stderr, run_session_id)
            )

            final_result_json, stderr_output = await asyncio.gather(
                stdout_task, stderr_task
            )
            await process.wait()
            return process.returncode, stderr_output, final_result_json

        cmd_base = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--model",
            model,
            "--verbose",
            "--allowedTools",
            ",".join(self._get_allowed_tools()),
        ]

        if self._permissions == FilePermissions.FULL_ACCESS:
            cmd_base.append("--dangerously-skip-permissions")

        if continue_conversation:
            logger.info(
                "Attempting to continue conversation with '-c' flag.",
                extra={"run_session_id": run_session_id, "run_completed": False},
            )
            cmd_with_c = ["claude", "-c"] + cmd_base[1:]
            return_code, stderr, result_obj = await _run_and_stream(cmd_with_c)

            if return_code != 0 and "No prior conversation history found" in stderr:
                logger.warning(
                    "Continuation failed as no history was found. Retrying immediately without '-c'.",
                    extra={
                        "run_session_id": run_session_id,
                        "run_completed": True,
                        "run_failed": True,
                    },
                )
                return_code, stderr, result_obj = await _run_and_stream(cmd_base)
        else:
            return_code, stderr, result_obj = await _run_and_stream(cmd_base)

        logger.info(
            f"Process finished with exit code {return_code}",
            extra={
                "run_session_id": run_session_id,
                "run_completed": True,
                "run_failed": True if return_code != 0 else False,
            },
        )

        if return_code != 0:
            raise ClaudeProcessError(
                f"CLI tool failed with exit code {return_code}. Stderr: {stderr}"
            )
        if not result_obj:
            raise ClaudeProcessError(
                "CLI tool finished but produced no final result object."
            )

        is_error = result_obj.get("is_error", True)
        subtype = result_obj.get("subtype")
        if is_error or subtype != "success":
            error_message = f"Claude returned a non-successful result. Subtype: '{subtype}', Is Error: {is_error}."
            logger.error(
                error_message,
                extra={
                    "run_session_id": run_session_id,
                    "run_completed": True,
                    "run_failed": True,
                },
            )
            raise ClaudeProcessError(error_message, result_data=result_obj)

        return result_obj.get("result", "")

    async def run_claude_code(
        self,
        prompt: str,
        directory: str,
        model: CLAUDE_CODE_MODELS = CLAUDE_CODE_MODELS.CLAUDE_SONNET_4,
        continue_conversation: bool = False,
    ) -> str:
        last_exception = None
        run_session_id = f"claude-{uuid.uuid4().hex[:8]}"
        for attempt in range(self._retries + 1):
            try:
                if attempt > 0:
                    wait_time = 2**attempt
                    logger.info(
                        f"Retrying in {wait_time} seconds... (Attempt {attempt + 1}/{self._retries + 1})",
                        extra={"run_session_id": run_session_id},
                    )
                    await asyncio.sleep(wait_time)
                return await self._run_claude_instance(
                    prompt, directory, run_session_id, model, continue_conversation
                )
            except (ClaudeProcessError, OSError) as e:
                last_exception = e
                logger.error(
                    f"Execution failed on attempt {attempt + 1}. Error: {e}",
                    extra={
                        "run_session_id": run_session_id,
                        "run_completed": True,
                        "run_failed": True,
                    },
                )

        raise ClaudeProcessError(
            f"All {self._retries + 1} attempts to run Claude failed."
        ) from last_exception


