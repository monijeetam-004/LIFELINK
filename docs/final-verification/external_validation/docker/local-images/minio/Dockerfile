FROM lifelink-base:noble-local-ca
# Official MinIO RELEASE.2025-04-22T22-12-26Z .deb and mcli RELEASE.2025-04-16T18-13-26Z .deb (GitHub release assets, sha256 verified).
COPY minio.deb mcli.deb /tmp/
RUN dpkg -i /tmp/minio.deb /tmp/mcli.deb && rm /tmp/*.deb && ln -s /usr/local/bin/mcli /usr/local/bin/mc && ln -s /usr/local/bin/mcli /usr/bin/mc
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
