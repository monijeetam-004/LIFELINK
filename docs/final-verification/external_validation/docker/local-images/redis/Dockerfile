FROM lifelink-base:noble-local-ca
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends redis-server && rm -rf /var/lib/apt/lists/*
EXPOSE 6379
CMD ["redis-server", "--bind", "0.0.0.0", "--protected-mode", "no", "--save", ""]
