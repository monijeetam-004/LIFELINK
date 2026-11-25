# Multi-stage build: compile the SPA with Node, serve the static bundle with
# nginx (non-root, SPA fallback to index.html). VITE_API_URL is baked in at
# build time -- pass it as a build arg.
# NODE_IMAGE / NGINX_IMAGE are overridable only so the image can be verified
# where the official registries are unreachable (see
# docs/final-verification/external_validation/docker); defaults unchanged.
ARG NODE_IMAGE=node:20.18-bookworm-slim
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.27-alpine
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM ${NGINX_IMAGE}
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
