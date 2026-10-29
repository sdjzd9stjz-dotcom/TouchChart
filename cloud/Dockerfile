FROM python:3.12-slim
WORKDIR /app
COPY touchchart.py cloud_server.py ./
USER 65534:65534
EXPOSE 8791
CMD ["python", "cloud_server.py", "--host", "0.0.0.0", "--port", "8791"]
