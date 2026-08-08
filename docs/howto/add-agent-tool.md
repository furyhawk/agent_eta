# How to: Add a New Agent Tool

## Overview

Agent tools let your AI agent perform actions — search the web, query databases, send emails, etc. Each tool is a Python function that the agent can call.

## Step-by-Step

### 1. Create the tool file

```python
# app/agents/tools/weather.py
```

### 2. Export from `app/agents/tools/__init__.py`

```python
from app.agents.tools.weather import get_weather
```

### 3. Register in your agent

### 4. Test it

Start the server and ask the agent: "What's the weather in Warsaw?"

## Tips

- Keep tools focused — one tool, one job
- Write clear docstrings — the agent uses them to decide when to call your tool
- Handle errors gracefully — return error messages as strings, don't raise exceptions
- For expensive operations, consider adding caching
