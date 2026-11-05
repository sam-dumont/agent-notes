# agent-notes server: the MCP server, plus the small tools the deployments run
# from the same image (render_config.py, gh_app_token.py). inbox_review.py is the
# hourly reviewer's code: the server's inbox tools use the same floor and moves. Dependencies come
# from hash-locked requirements (uv export, from uv.lock) and the tests run in
# the build: a red test means no image. Build context: the repo root
# (docker build -f server/Dockerfile .), for the shared CouchDB template.
FROM docker.io/library/python:3.13-slim-trixie@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1 AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /venv && python -m venv /testvenv
COPY server/requirements.txt server/requirements-dev.txt /src/
RUN /venv/bin/pip install --require-hashes --only-binary=:all: -r /src/requirements.txt \
 && /testvenv/bin/pip install --require-hashes --only-binary=:all: -r /src/requirements-dev.txt
COPY server/*.py reviewer/inbox_review.py reviewer/test_inbox_review.py sync/couchdb.ini.tmpl /src/
# pytest only in this stage: the runtime image doesn't ship it.
RUN cd /src && PYTHONPATH=/venv/lib/python3.13/site-packages /testvenv/bin/pytest -q -p no:cacheprovider

FROM docker.io/library/python:3.13-slim-trixie@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1
COPY --from=build /venv /venv
COPY server/server.py server/access.py server/telemetry.py server/gh_app_token.py server/render_config.py reviewer/inbox_review.py sync/couchdb.ini.tmpl /app/
ENV PATH=/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Same uid as the sync daemon and git-sync: one owner for every file in the notes.
USER 1000:1000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["python", "/app/server.py"]
