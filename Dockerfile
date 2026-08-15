FROM python:3.12-slim

WORKDIR /app

# Copy only what the build needs before installing, so dependency layers
# stay cached when application code changes without a dependency bump.
COPY pyproject.toml README.md ./
COPY kessler ./kessler
COPY docs ./docs

RUN pip install --no-cache-dir .

ENV PORT=8080
EXPOSE 8080

# Fly.io (and most PaaS targets) inject PORT at runtime; default to 8080
# for local `docker run` without -e PORT=...
CMD ["sh", "-c", "uvicorn kessler.api:app --host 0.0.0.0 --port ${PORT:-8080}"]
