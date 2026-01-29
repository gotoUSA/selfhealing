# OpenTelemetry Collector with wget for healthcheck
#
# 기본 otel-collector-contrib 이미지는 scratch 기반으로 셸/wget/curl이 없음
# Docker Compose 환경에서 헬스체크를 위해 Alpine 기반으로 빌드
#
# 참고: Kubernetes 환경에서는 외부 livenessProbe/readinessProbe로 체크 가능하므로
# 원본 이미지(otel/opentelemetry-collector-contrib) 사용 가능

ARG OTEL_VERSION=0.96.0

# Stage 1: 원본 이미지에서 바이너리 복사
FROM otel/opentelemetry-collector-contrib:${OTEL_VERSION} AS collector

# Stage 2: Alpine 기반 이미지에 바이너리 복사
FROM alpine:3.19

# 필수 패키지 설치 (wget은 busybox에 포함, ca-certificates는 TLS 연결용)
RUN apk add --no-cache ca-certificates wget

# 바이너리 복사
COPY --from=collector /otelcol-contrib /otelcol-contrib

# 비root 사용자로 실행 (보안)
RUN adduser -D -u 10001 otel
USER otel

# 헬스체크 포트
EXPOSE 4317 4318 8888 13133

ENTRYPOINT ["/otelcol-contrib"]
CMD ["--config", "/etc/otelcol/otel-collector-config.yml"]
