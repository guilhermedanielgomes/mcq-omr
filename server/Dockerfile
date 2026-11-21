# Run the MCQ-OMR server in a container (e.g. on Unraid).
#   docker build -t mcq-omr .
#   docker run -d --name mcq-omr -p 8000:8000 -v /mnt/user/appdata/mcq-omr:/app/data mcq-omr
# Optional:  -e MCQ_TOKEN=some-secret
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV MCQ_DATA=/app/data
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
