Access Claude Code via API or SlackBot

Plan:
1. API Gateway -> FastAPI -> 2 end points (index + modify)
2. Redis Cache for Codebases + Lock to prevent workers from working on same branch (dif bw index and modify) + Anthropic rate limit check
3. Message broker for different calls -> RabbitMQ
4. Woekers -> Celery for getting codebase (may be cached) and running claude code



Issues to Solve:
1. Stream Json has different message types -> System, Assistant, Result -> some filter
2. How to read the messages line by line, I want to read it json by json
3. Do I need to queue the output ?