FROM python:3.12-slim

WORKDIR /app

COPY apps/chatgpt-bridge/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY apps/chatgpt-bridge/bridge.py /app/bridge.py

EXPOSE 8788

CMD ["python", "-u", "bridge.py"]
