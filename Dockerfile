# ArchFlow — governance API + web UI in one container.
#
#   docker build -t archflow .
#   docker run -p 8000:8000 -v archflow-data:/data archflow
#
# Configure via ARCHFLOW_* environment variables (see .env.example).
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --create-home archflow \
    && mkdir /data && chown archflow /data

USER archflow
ENV ARCHFLOW_DATABASE_URL=sqlite:////data/archflow.db \
    ARCHFLOW_ARTIFACTS_DIR=/data/artifacts
VOLUME /data
EXPOSE 8000

CMD ["uvicorn", "archflow.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
