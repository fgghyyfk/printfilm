# 管理端：Vite 构建后由 nginx 托管，请求走同源 /api
FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# 子路径部署（如 /admin/）时由 compose 传入
ARG VITE_ADMIN_BASE=/
ENV VITE_ADMIN_BASE=$VITE_ADMIN_BASE
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80
