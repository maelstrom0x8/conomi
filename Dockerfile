FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY conomi ./conomi

RUN pip install --no-cache-dir .

EXPOSE 8100

CMD ["uvicorn", "conomi.app:app", "--host", "0.0.0.0", "--port", "8100"]
