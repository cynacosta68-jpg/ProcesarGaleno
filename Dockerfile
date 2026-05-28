# ─────────────────────────────────────────────────────────────
#  Imagen oficial de Playwright para Python — Ubuntu 22.04
#  Chromium + todas sus dependencias ya incluidas
# ─────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV STREAMLIT_SERVER_HEADLESS=true

WORKDIR /app

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY . .

EXPOSE 8501

# Railway inyecta $PORT automáticamente
CMD streamlit run app.py \
    --server.port=${PORT:-8501} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
