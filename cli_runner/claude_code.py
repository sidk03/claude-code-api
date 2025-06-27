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
            if not line:
                continue
            line = line.decode("utf-8").strip()
            try:
                data = json.loads(line)
                msg_type = data.get("type")
                claude_session_id = data.get("session_id")
                log_running = {
                            "claude_session_id": claude_session_id,
                            "run_session_id": run_session_id,
                            "status": "running",
                        }
                log_error = {"run_session_id": run_session_id,
                            "status": "running"}

                if msg_type == "system":
                    cwd = data.get('cwd', 'N/A')
                    logger.info(
                        f"[SYSTEM] Initialized in directory: {cwd}",
                        extra=log_running,
                    )

                elif msg_type == "assistant":
                    content_list = data.get("message", {}).get("content", [])
                    for content_item in content_list:
                        item_type = content_item.get("type")
                        if item_type == "tool_use":
                            tool_name = content_item.get('name', 'UnknownTool')
                            tool_input = content_item.get('input', {})
                            input_str = ", ".join([f"{k}='{v}'" for k, v in tool_input.items()])
                            display_input = (input_str[:250] + '...') if len(input_str) > 250 else input_str
                            logger.info(f"[ASSISTANT] Tool Use: {tool_name}({display_input})", extra=log_running)
                        elif item_type == "text":
                            text = content_item.get("text", "").strip()
                            if text:
                                display_text = (text[:250] + '...') if len(text) > 250 else text
                                logger.info(f"[ASSISTANT] Response: {display_text}", extra=log_running)
                        elif item_type == "thinking":
                            logger.info("[ASSISTANT] Thinking...", extra=log_running)
                   
                elif msg_type == "result":
                    logger.info(f"[FINAL MESSAGE] Received.", extra=log_running)
                    return data
                
                elif msg_type != "user":
                    logger.debug(f"[OTHER] Unhandled message type: {line}", extra=log_running)

            except json.JSONDecodeError:
                logger.warning(
                    f"Received non-JSON line from stdout: {line}",
                    extra=log_error,
                )
            except Exception as e:
                logger.error(
                    f"Error processing stream line: {line}. Error: {e}",
                    extra=log_error,
                )

        return None

    async def _stream_stderr_handler(
        self, stream: asyncio.StreamReader, run_session_id: str
    ) -> str:
        stderr_output = await stream.read()
        decoded_stderr = stderr_output.decode('utf-8').strip()
        if decoded_stderr:
            for line in decoded_stderr.splitlines():
                 logger.error(f"[STDERR] {line}", extra={'run_session_id': run_session_id, 'status': 'running'})
        return decoded_stderr

    async def _run_claude_instance(
        self,
        prompt: str,
        directory: str,
        run_session_id: str,
        model: CLAUDE_CODE_MODELS = CLAUDE_CODE_MODELS.CLAUDE_SONNET_4,
        continue_conversation: bool = False,
    ) -> str:
        log_extra = {"run_session_id": run_session_id}
        async def _run_and_stream(
            cmd_args: list[str],
        ) -> tuple[int, str, Optional[dict]]:
            logger.debug(
                f"Executing command: {' '.join(cmd_args)}",
                extra={**log_extra, 'status': 'running'},
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
                extra={**log_extra, 'status': 'running'},
            )
            cmd_with_c = ["claude", "-c"] + cmd_base[1:]
            return_code, stderr, result_obj = await _run_and_stream(cmd_with_c)

            if return_code != 0 and "No prior conversation history found" in stderr:
                logger.warning(
                    "Continuation failed as no history was found. Retrying immediately without '-c'.",
                    extra={**log_extra, 'status': 'running'},
                )
                return_code, stderr, result_obj = await _run_and_stream(cmd_base)
        else:
            return_code, stderr, result_obj = await _run_and_stream(cmd_base)

        logger.info(
            f"Process finished with exit code {return_code}",
            extra={**log_extra, 'status': "running"},
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
                extra={**log_extra, 'status': 'failed'},
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
        logger.info("Starting Claude execution.", extra={'run_session_id': run_session_id, 'status': 'starting'})
        for attempt in range(self._retries + 1):
            log_extra = {'run_session_id': run_session_id, 'attempt': attempt + 1}
            try:
                if attempt > 0:
                    wait_time = 2**attempt
                    logger.info(
                        f"Retrying in {wait_time} seconds... (Attempt {attempt + 1}/{self._retries + 1})",
                        extra={**log_extra, "status":"retrying"},
                    )
                    await asyncio.sleep(wait_time)

                result =  await self._run_claude_instance(
                    prompt, directory, run_session_id, model, continue_conversation
                )
                logger.info("Claude execution successful.", extra={**log_extra, 'status': 'success'})
                return result
            except (ClaudeProcessError, OSError) as e:
                last_exception = e
                logger.error(
                    f"Execution failed on attempt {attempt + 1}. Error: {e}",
                    extra={**log_extra, 'status': 'failed'}
                )
        logger.critical(f"All {self._retries + 1} attempts failed. Aborting.", extra={'run_session_id': run_session_id, 'status': 'failed'})
        raise ClaudeProcessError(
            f"All {self._retries + 1} attempts to run Claude failed."
        ) from last_exception

