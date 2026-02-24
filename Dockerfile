FROM python:3.12-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY leaderboard.py .

EXPOSE 8080

CMD ["python", "leaderboard.py"]
