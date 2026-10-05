FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 STUDIO_DATA=/data
COPY studio/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 1000 --create-home studio
COPY studio /app/studio
COPY config /app/config
COPY workflows /app/workflows
RUN mkdir /data && chown studio:studio /data
USER studio
EXPOSE 7878
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7878/api/health',timeout=3)"
CMD ["python", "-m", "studio.server"]
