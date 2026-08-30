# The agent shells out to `docker compose` against the host daemon, so the image
# needs the docker CLI + the compose v2 plugin. Copy both from the official
# docker CLI image rather than wiring up an apt repo.
FROM docker:27-cli AS dockercli

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose \
     /usr/local/libexec/docker/cli-plugins/docker-compose

WORKDIR /app

# setuptools needs the package dir present during metadata prep, so copy the
# sources before installing (rather than pyproject-then-code).
COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

EXPOSE 8000

CMD ["python", "-m", "app.main"]
