# 1. Usar la imagen oficial de Microsoft Playwright optimizada para Python
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# 2. Establecer la carpeta de trabajo dentro del servidor
WORKDIR /app

# 3. Copiar el instalador de librerías y ejecutarlo
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copiar todo el código del proyecto al servidor
COPY . .

# 5. Informar el puerto que utilizará Streamlit
EXPOSE 8501

# 6. Comando definitivo para arrancar la web en producción
CMD ["streamlit", "run", "app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]