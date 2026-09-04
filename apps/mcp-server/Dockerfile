FROM python:3.12-slim

WORKDIR /app

COPY apps/mcp-server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY apps/mcp-server/src /app/src

EXPOSE 8000

CMD ["python", "-m", "src.server"]
