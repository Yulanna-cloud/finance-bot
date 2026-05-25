FROM python:3.10

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r finance_bot/requirements.txt

CMD ["python", "finance_bot/main.py"]
