FROM lifelink-base:noble-local-ca
# Official Node.js 20.18.3 linux-x64 (+ npm 10.8.2) binary, distributed through the npm registry
# as the package node-linux-x64 (nodejs.org itself is not reachable here).
COPY node-linux-x64 /opt/node
RUN ln -s /opt/node/bin/node /usr/local/bin/node \
 && /opt/node/bin/node /opt/node/lib/node_modules/npm/bin/npm-cli.js --version >/dev/null 2>&1 || true
ENV PATH=/opt/node/bin:$PATH
# The verification sandbox intercepts TLS; trust its proxy CA (sandbox only).
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/sandbox-ca.crt
