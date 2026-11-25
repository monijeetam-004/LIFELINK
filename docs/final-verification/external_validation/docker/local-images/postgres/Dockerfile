FROM lifelink-base:noble-local-ca
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends postgresql-16 && rm -rf /var/lib/apt/lists/*
ENV PATH=/usr/lib/postgresql/16/bin:$PATH PGDATA=/var/lib/postgresql/data
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /var/lib/postgresql/data /var/run/postgresql && chown -R postgres:postgres /var/lib/postgresql /var/run/postgresql
VOLUME /var/lib/postgresql/data
EXPOSE 5432
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
