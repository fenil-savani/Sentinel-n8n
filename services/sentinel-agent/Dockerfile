FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# claude CLI, only used when LLM_PROVIDER=claude_cli (a Claude Code
# subscription instead of ANTHROPIC_API_KEY — see app/llm/claude_cli.py).
# Adds ~250-300MB. If you only ever use the default "anthropic" provider,
# this layer is simply unused at runtime. Alternative for local/dev use:
# skip this layer and bind-mount a host-installed `claude` binary into the
# container instead, pointing CLAUDE_CLI_BIN at the mounted path.
#
# @anthropic-ai/claude-code requires Node >=22; Debian trixie's `apt nodejs`
# package only provides Node 20 (confirmed via npm's EBADENGINE warning on a
# plain `apt install nodejs npm`), so Node is pulled from NodeSource instead.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install --no-install-recommends -y nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

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
