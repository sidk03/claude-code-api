Access Claude Code via API or SlackBot

Plan:
1. API Gateway -> FastAPI -> 2 end points (index + modify)
2. Redis Cache for Codebases + Lock to prevent workers from working on same branch (dif bw index and modify) + Anthropic rate limit check
3. Message broker for different calls -> RabbitMQ
4. Woekers -> Celery for getting codebase (may be cached) and running claude code
