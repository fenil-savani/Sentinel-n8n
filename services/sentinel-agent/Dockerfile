FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# run_python executes model-generated code. It runs as this unprivileged user,
# in a per-call temp cwd, under a wall-clock timeout and RLIMIT caps. The
# container's only mounts are read-only (/prompts, /reference), so the blast
# radius is the container itself. See app/tools/python_exec.py.
RUN useradd --create-home --uid 10001 agent \
    && chown -R agent:agent /srv
USER agent

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
