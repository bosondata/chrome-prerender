FROM ubuntu:16.04

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

RUN echo 'deb http://ppa.launchpad.net/deadsnakes/ppa/ubuntu xenial main' > /etc/apt/sources.list.d/deadsnakes-ubuntu-ppa-xenial.list \
    && apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 6A755776 \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    libffi-dev \
    python3.6 \
    python3.6-dev \
    libssl-dev \
    && curl -sSL https://bootstrap.pypa.io/get-pip.py | python3.6 \
    && python3.6 -m pip install -U pip setuptools wheel

RUN python3.6 -m pip install -U prerender diskcache minio \
    && rm -rf /var/lib/apt/list/* /tmp/* /var/tmp/*

EXPOSE 3000

ENTRYPOINT /usr/local/bin/prerender
