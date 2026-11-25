FROM lifelink-base:noble-local-ca
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nginx && rm -rf /var/lib/apt/lists/* \
 && rm -f /etc/nginx/sites-enabled/default && mkdir -p /usr/share/nginx/html
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
