# syntax=docker/dockerfile:1
# Bundle the application, automatic HTTPS proxy, bootstrap, and process reaper in one image.
FROM docker.io/library/caddy:2.11.7 AS caddy
FROM docker.io/library/python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
LABEL org.opencontainers.image.source="https://github.com/tijme/flaretrace" \
      org.opencontainers.image.title="Flaretrace" \
      org.opencontainers.image.description="Self-contained tracking links and permission-based evidence collection" \
      org.opencontainers.image.licenses="GPL-3.0-only"
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
COPY deploy/licenses/CADDY-LICENSE /usr/share/doc/caddy/LICENSE
WORKDIR /app
COPY pyproject.toml README.md LICENSE.md ./
COPY src ./src
# The optional build secret is only needed in managed environments with a TLS proxy.
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir .; \
    else pip install --no-cache-dir .; fi
RUN useradd --uid 10001 --create-home trace \
    && mkdir -p /data /run/flaretrace \
    && chown trace:trace /data /run/flaretrace
# Only bootstrap uses root; both servers permanently drop to the selected non-root UID/GID.
EXPOSE 80/tcp 443/tcp 443/udp
VOLUME ["/data"]
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=5s --timeout=5s --start-period=15s --retries=6 CMD ["flaretrace", "healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "flaretrace"]
CMD ["serve"]
