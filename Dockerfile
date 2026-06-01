FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# An editable install needs the package sources present at install time, so the
# sources are copied BEFORE `pip install -e`. A change under app/ therefore
# re-runs the install layer; slimming the runtime image (non-editable install,
# drop tests/ + dev extras) is noted in the README as a deploy follow-up.
COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY tests ./tests
RUN pip install -e ".[dev]"

EXPOSE 8000

# Default command; docker-compose overrides per service (api / worker).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
