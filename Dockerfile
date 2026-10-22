FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Bratislava
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lovec ./lovec
COPY config.yaml .
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"
CMD ["python", "-m", "lovec", "serve"]
