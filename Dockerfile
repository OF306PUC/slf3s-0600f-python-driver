FROM python:3.11-slim

WORKDIR /app/slf3s

COPY slf3s/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY slf3s/ .

# The driver version that wrote each data file (run.sh passes `git rev-parse`).
# Declared last: a new commit must not invalidate the cached pip layer above.
ARG DRIVER_COMMIT=unknown
ENV DRIVER_COMMIT=${DRIVER_COMMIT}

ENTRYPOINT ["python3", "main.py"]
