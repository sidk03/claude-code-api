Bulletproof SDK for Claude Code, handles internal errors in structure or response type and has retry policy.

Running Instructions,
1. Make sure claude code is setup locally
2. Make a single instance of the claude runner with the desired file permissions and retry count
3. Set desired log level for stdout in the logging config, info will show all updates. Also set the desired file path for the log directory in the config_logging function.
4. Run as many session concurrently, use a task group or asyncio.gather to collect responses
5. Except and handle the Claude Exception on the slim chance that it fails all retries


Known Bug,
Claude Code currently truncates json output longer then 8000 tokens ??? Should be fixed on next release, beware if you are producing very long output. This is temporarly fixed by buffering json lines but is not always valid.
