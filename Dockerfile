FROM python:3.10
WORKDIR /app

# libzbar0 — чтение QR-кодов на чеках; fonts-dejavu-core — кириллический шрифт для отчёта-картинки
RUN apt-get update && apt-get install -y libzbar0 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8080
CMD ["python", "bot.py"]
