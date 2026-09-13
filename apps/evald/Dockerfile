# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

FROM base AS deps
COPY apps/evald/pyproject.toml ./
# Install dependencies only; the source is copied in the runtime stage so a
# code change does not invalidate the dependency layer.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.32" \
                               "pydantic>=2.10" "pydantic-settings>=2.6"

FROM deps AS runtime
COPY apps/evald/pyproject.toml ./
COPY apps/evald/src ./src
COPY config /config
ENV PYTHONPATH=/app/src

ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

RUN useradd --create-home --uid 1000 evald && chown -R evald /app
USER evald
EXPOSE 8000
CMD ["uvicorn", "evald.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
