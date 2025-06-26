import asyncio
import os
import json
from pathlib import Path
import shutil
from common.logging_config import config_logging
import logging
from enum import StrEnum
from typing import Optional

config_logging("test_logs.jsonl")
logger = logging.getLogger(__name__)
logger = logging.LoggerAdapter(logger=logger, extra={"test_session": 10})


class ClaudeProcessError(Exception):
    pass


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
        self._retries = retries

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

    async def _stream_stdout_handler(self, stream: asyncio.StreamReader) -> dict:
        async for line in stream:
            line = line.decode("utf-8").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                msg_type = data.get("type")
                session_id = data.get("session_id")

                if msg_type == "system":
                    content = str(data)
                    logger.info(
                        f"[{msg_type.upper()}] {content}",
                        extra={"claude_session": session_id},
                    )
                elif msg_type == "assistant":
                    content = data.get("message", {}).get("content", str(data))
                    logger.info(f"[{msg_type.upper()}] {content}")
                elif msg_type == "result":
                    result = data.get("result", str(data))
                    logger.info(
                        f"Final Message Recieved : {result}",
                        extra={"claude_session": session_id},
                    )
                    return data
                elif msg_type == "user":
                    pass
                else:
                    logger.debug(
                        f"[OTHER] Received unhandled message type: {line}",
                        extra={"claude_session": session_id},
                    )
            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON line from stdout: {line}")
            except Exception as e:
                logger.error(f"Error processing stream line: {line}. Error: {e}")

        return None

    async def _stream_stderr_handler(self, stream: asyncio.StreamReader):
        async for line in stream:
            line = line.decode("utf-8").strip()
            if not line:
                continue
            logger.error(line)

    async def _run_claude_instance(
        self,
        prompt: str,
        directory: str,
        model: CLAUDE_CODE_MODELS = CLAUDE_CODE_MODELS.CLAUDE_SONNET_4,
        continue_conversation: bool = False,
    ) -> dict:
        cmd_args = [
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
        if continue_conversation:
            cmd_args.insert(1, "-c")
        if self._permissions == FilePermissions.FULL_ACCESS:
            cmd_args.append("--dangerously-skip-permissions")

        process = await asyncio.create_subprocess_exec(
            *cmd_args,
            cwd=directory,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )

        stdout_task = asyncio.create_task(self._stream_stdout_handler(process.stdout))
        stderr_task = asyncio.create_task(self._stream_stderr_handler(process.stderr))

        result, _ = await asyncio.gather(stdout_task, stderr_task)
        await process.wait()

        logger.info(f"Process finished with exit code {process.returncode}")

        if process.returncode != 0:
            raise ClaudeProcessError(
                f"Claude CLI failed with exit code {process.returncode}"
            )
        if not result:
            raise ClaudeProcessError(
                "Claude CLI finished successfully but produced no final result."
            )

        return result

    async def run_claude_code(
        self,
        prompt: str,
        directory: str,
        model: CLAUDE_CODE_MODELS = CLAUDE_CODE_MODELS.CLAUDE_SONNET_4,
        continue_conversation: bool = False,
    ):
        last_exception = None
        for attempt in range(self._retries + 1):
            try:
                if attempt > 0:
                    wait_time = 2**attempt
                    logger.info(
                        f"Retrying in {wait_time} seconds... (Attempt {attempt + 1}/{self._retries + 1})"
                    )
                    await asyncio.sleep(wait_time)
                return await self._run_claude_instance(
                    prompt, directory, model, continue_conversation
                )
            except (ClaudeProcessError, OSError) as e:
                last_exception = e
                logger.error(f"Execution failed on attempt {attempt + 1}. Error: {e}")

        raise ClaudeProcessError(
            f"All {self._retries + 1} attempts to run Claude failed."
        ) from last_exception


async def main():
    """Example usage of the ClaudeCodeRunner class."""
    # Create a temporary directory for the execution
    temp_dir = "claude_test_run"
    os.makedirs(temp_dir, exist_ok=True)

    # Create a dummy file for claude to read
    with open(os.path.join(temp_dir, "hello.txt"), "w") as f:
        f.write("Hello from the file!")

    print("--- Running with READ_ONLY permissions ---")
    try:
        # Initialize the runner with read-only permissions and 1 retry
        read_only_runner = ClaudeCodeRunner(
            permissions=FilePermissions.READ_ONLY, retries=1
        )
        prompt = "Read the file hello.txt and tell me its content."
        result = await read_only_runner.run_claude_code(
            prompt=prompt, directory=temp_dir
        )

        print("\n--- FINAL RESULT ---")
        print(result)
        print("--------------------")

    except ClaudeProcessError as e:
        print(f"\n--- EXECUTION FAILED ---")
        print(f"Error: {e}")
        print("------------------------")
    except FileNotFoundError:
        print("\n--- SETUP FAILED ---")
        print(
            "Error: 'claude' command not found. Please ensure it's installed and in your PATH."
        )
        print("--------------------")

    # Clean up the dummy directory and file
    shutil.rmtree(temp_dir)  # Uncomment to clean up automatically


if __name__ == "__main__":
    # Note: To run this example, you must have the 'claude' CLI tool installed
    # and authenticated on your system.
    asyncio.run(main())
