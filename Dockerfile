FROM python:3.10.13-alpine3.19

WORKDIR /app

COPY requirements.txt /app

RUN pip install --no-cache-dir -r requirements.txt

COPY run.py /app
