# syntax=docker/dockerfile:1

FROM python:3.11-slim

WORKDIR /app

# Install dependencies early so they are cached
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY sleeper_trades.py webapp.py ./
COPY templates ./templates
COPY static ./static

# Pre-fetch Sleeper players.json at build time for caching
RUN python -c "import sleeper_trades; import asyncio; asyncio.run(sleeper_trades.get_players())"

EXPOSE 8000

# Run FastAPI with uvicorn
CMD ["uvicorn", "webapp:app", "--host", "0.0.0.0", "--port", "8000"]
