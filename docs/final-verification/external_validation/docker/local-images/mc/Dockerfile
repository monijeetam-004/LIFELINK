FROM lifelink-base:noble-local-ca
COPY mcli.deb /tmp/
RUN dpkg -i /tmp/mcli.deb && rm /tmp/mcli.deb && ln -s /usr/local/bin/mcli /usr/bin/mc
ENTRYPOINT ["/usr/bin/mc"]
