FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends redis-tools && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/flask-app

COPY ./requirements.txt .

RUN pip install -r requirements.txt

COPY . .

EXPOSE 5000