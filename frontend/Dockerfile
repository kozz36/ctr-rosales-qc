# Frontend image — build the Vite SPA, serve it with nginx + reverse-proxy /api.
# Stage 1: build the static bundle. Stage 2: lean nginx runtime.

FROM node:20-alpine AS build
WORKDIR /app
# Install deps first (cached layer) then build.
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine AS runtime
# SPA history-fallback + /api reverse proxy to the backend service.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1
