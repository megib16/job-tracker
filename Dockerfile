FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src/job_tracker/ ./src/job_tracker/
RUN python -m pip install --no-cache-dir .
USER 10001:10001
ENTRYPOINT ["job-tracker"]
CMD ["--dry-run"]
