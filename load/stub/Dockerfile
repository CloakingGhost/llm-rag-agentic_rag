# 가짜 OpenAI 스텁 (부하테스트 전용). 의존성은 fastapi + uvicorn 뿐이다
FROM python:3.13-slim
RUN pip install --no-cache-dir fastapi uvicorn
COPY llm_stub.py /app/llm_stub.py
ENV PORT=8080
CMD ["python", "/app/llm_stub.py"]
