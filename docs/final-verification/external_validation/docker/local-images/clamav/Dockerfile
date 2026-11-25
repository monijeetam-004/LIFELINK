FROM lifelink-base:noble-local-ca
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends clamav-daemon clamav-testfiles netcat-openbsd python3 && rm -rf /var/lib/apt/lists/*
COPY build_local_signatures.py /opt/build_local_signatures.py
RUN mkdir -p /var/lib/clamav /run/clamav && python3 /opt/build_local_signatures.py /var/lib/clamav /usr/share/clamav-testfiles \
 && printf 'Foreground yes\nTCPSocket 3310\nTCPAddr 0.0.0.0\nDatabaseDirectory /var/lib/clamav\nStreamMaxLength 25M\nUser root\n' > /etc/clamav/clamd-local.conf
RUN printf '#!/bin/sh\n[ "$(echo PING | nc -w 3 127.0.0.1 3310)" = "PONG" ]\n' > /usr/local/bin/clamdcheck.sh && chmod +x /usr/local/bin/clamdcheck.sh
EXPOSE 3310
CMD ["clamd", "-c", "/etc/clamav/clamd-local.conf"]
