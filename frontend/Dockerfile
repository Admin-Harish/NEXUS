FROM ghcr.io/cirruslabs/flutter:stable AS build
ARG API_BASE_URL=http://localhost:8000
WORKDIR /app
COPY pubspec.yaml .
RUN flutter pub get
COPY . .
RUN sed -i "s|__API_BASE_URL__|${API_BASE_URL}|g" lib/config.dart
RUN flutter build web --release

FROM nginx:1.27-alpine
COPY --from=build /app/build/web /usr/share/nginx/html
EXPOSE 80
