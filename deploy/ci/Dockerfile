# syntax=docker/dockerfile:1
# Build reproducible backend and multi-browser test images, separate from production.
ARG PYTHON_VERSION=3.12
FROM docker.io/library/node:24-bookworm-slim AS node
FROM docker.io/library/caddy:2.11.7 AS caddy

FROM docker.io/library/python:${PYTHON_VERSION}-slim-bookworm AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CI=1 CADDY_BINARY=/usr/local/bin/caddy
WORKDIR /workspace
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
COPY pyproject.toml README.md LICENSE.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[test]'
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY tests ./tests
COPY deploy ./deploy
COPY compose.yaml ./
COPY .github ./.github
COPY assets ./assets
RUN chmod -R a+rX /workspace
ENTRYPOINT ["python", "tests/ci/run.py"]

FROM mcr.microsoft.com/playwright/python:v1.64.1-noble AS ui
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CI=1 CADDY_BINARY=/usr/local/bin/caddy
WORKDIR /workspace
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
COPY pyproject.toml README.md LICENSE.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[test,browser]'
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY tests ./tests
COPY deploy ./deploy
COPY compose.yaml ./
COPY .github ./.github
COPY assets ./assets
RUN chmod -R a+rX /workspace
ENTRYPOINT ["python", "tests/ci/run.py"]
