# Postgres 16 + pgvector + Apache AGE
FROM postgres:16

RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    postgresql-server-dev-16 \
    flex \
    bison \
    && rm -rf /var/lib/apt/lists/*

# pgvector v0.7.0
WORKDIR /tmp
RUN git clone --branch v0.7.0 https://github.com/pgvector/pgvector.git \
    && cd pgvector && make && make install

# Apache AGE (graph extension)
RUN git clone --branch PG16 https://github.com/apache/age.git \
    && cd age && make install

WORKDIR /
RUN rm -rf /tmp/age /tmp/pgvector
