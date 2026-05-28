# ─────────────────────────────────────────────────────────────
#  Base: Python 3.11 slim
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Evitar prompts interactivos de apt
ENV DEBIAN_FRONTEND=noninteractive
# Playwright descarga Chromium aquí dentro del contenedor
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# Streamlit no abre browser en servidor
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV STREAMLIT_SERVER_HEADLESS=true
# Python no genera .pyc ni bufferiza stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ── Dependencias mínimas de sistema (Chromium las instala playwright) ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Instalar dependencias Python ───────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Instalar Chromium + todas sus dependencias de sistema ──────────────
# --with-deps instala automáticamente libnss3, libatk, libgbm, etc.
RUN playwright install chromium --with-deps

# ── Copiar código de la aplicación ────────────────────────────────────
COPY . .

# Puerto expuesto (Railway sobreescribe con $PORT en runtime)
EXPOSE 8501

# ── Comando de inicio ─────────────────────────────────────────────────
# Railway inyecta $PORT automáticamente en cada deploy
CMD streamlit run app_galeno.py \
    --server.port=${PORT:-8501} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
