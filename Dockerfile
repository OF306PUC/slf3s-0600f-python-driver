FROM python:3.11-slim

WORKDIR /app/slf3s

COPY slf3s/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY slf3s/ .

ENTRYPOINT ["python3", "main.py"]
