FROM python:3.11-slim
RUN pip install --no-cache-dir psycopg2-binary==2.9.10
COPY shard_emu.py /bench/shard_emu.py
WORKDIR /bench
ENTRYPOINT ["python", "/bench/shard_emu.py"]
