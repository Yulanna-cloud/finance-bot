FROM python:3.10
WORKDIR /app

# Системная библиотека для чтения QR-кодов
RUN apt-get update && apt-get install -y libzbar0 && rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8080
CMD ["python", "bot.py"]
