# VF Graph Intelligence — imagem de deploy (FastAPI/uvicorn)
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e ".[research]"
ENV VF_OSINT_DB=/tmp/vf_osint.db
ENV CORS_ORIGINS=*
EXPOSE 8000
CMD ["sh", "-c", "uvicorn vf_osint.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
