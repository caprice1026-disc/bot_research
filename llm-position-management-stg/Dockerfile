FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home app
USER app
ENV PORT=8080
CMD ["sh", "-c", "exec gunicorn --bind :${PORT} --workers 1 --threads 1 --timeout 290 'position_stg.app:create_app()'"]
