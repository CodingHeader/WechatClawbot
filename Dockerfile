FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
ENV PYTHONPATH=/app/src

VOLUME ["/app/data"]
EXPOSE 8088

CMD ["python", "-m", "wechat_clawbot", "serve"]
