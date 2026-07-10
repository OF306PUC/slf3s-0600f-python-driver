FROM python:3.11-slim

WORKDIR /app/raspberry

COPY raspberry/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY raspberry/ .

ENTRYPOINT ["python3", "main.py"]
