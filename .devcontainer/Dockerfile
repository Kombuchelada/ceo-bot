# Dev sandbox for running Claude Code with --dangerously-skip-permissions.
# Everything Claude can do is bounded by this container.
FROM python:3.12-bookworm

ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_VERSION=20

# Base toolchain
RUN apt-get update && apt-get install -y --no-install-recommends \
        sudo \
        git \
        curl \
        ca-certificates \
        gnupg \
        lsb-release \
        unzip \
        less \
        ripgrep \
        fd-find \
        jq \
        tree \
        vim \
        nano \
        zsh \
        build-essential \
        sqlite3 \
        ffmpeg \
        tini \
        openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s "$(which fdfind)" /usr/local/bin/fd

# Node.js (for Claude Code CLI). Stick to the LTS line — the 22.22.2 nodesource
# build ships with a broken bundled npm (missing promise-retry).
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Railway CLI (the install script needs bash, not sh)
RUN curl -fsSL https://railway.com/install.sh | bash \
    && mv /root/.railway/bin/railway /usr/local/bin/railway

# DigitalOcean CLI
RUN curl -fsSL "https://github.com/digitalocean/doctl/releases/download/v1.118.0/doctl-1.118.0-linux-$(dpkg --print-architecture).tar.gz" \
        -o /tmp/doctl.tar.gz \
    && tar -xzf /tmp/doctl.tar.gz -C /usr/local/bin \
    && rm /tmp/doctl.tar.gz

# Python tooling
RUN pip install --upgrade pip uv ruff pytest pytest-asyncio

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Non-root user with passwordless sudo
RUN groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} --create-home --shell /usr/bin/zsh ${USERNAME} \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

USER ${USERNAME}
WORKDIR /workspace

# Persisted shell/state directories the bind mounts in devcontainer.json hook into
RUN mkdir -p /home/${USERNAME}/.claude /home/${USERNAME}/.config /home/${USERNAME}/.cache

# Friendly zsh prompt
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended || true

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
