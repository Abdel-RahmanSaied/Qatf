# Dependencies only. Split out so the dev image can stop here and never pay
# for a production bundle it will not serve.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# Dev target: the Vite dev server, for `docker-compose.dev.yaml`, which
# bind-mounts the source over /app. No source is COPYed in — the mount IS the
# source, which is the whole point: edit a file on the host and HMR pushes it
# into the browser with no rebuild and no restart.
#
# --host binds 0.0.0.0 so the port mapping reaches it; without it Vite listens
# on localhost inside the container and the mapped port answers nothing.
FROM deps AS dev
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# Build stage: strict type check + bundle.
FROM deps AS build
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# Runtime: static files + the /api proxy. No node in the final image.
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
