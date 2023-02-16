FROM python:3.9.16-alpine3.17

WORKDIR /app

COPY requirements.txt /app

RUN pip install --no-cache-dir -r requirements.txt

COPY run.py /app
