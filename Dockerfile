FROM python:3.9-slim

LABEL org.opencontainers.image.source https://github.com/jonathanjuursema/podimo-to-rss

ENV PODIMO_USERNAME="account@example.com"
ENV PODIMO_PASSWORD="correct horse battery staple"

WORKDIR /app

COPY src .

RUN pip install --no-cache-dir --upgrade -r requirements.txt

RUN export PATH="$PATH:/app"

CMD ["./run.sh"]

EXPOSE 80/tcp