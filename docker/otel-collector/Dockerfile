# OpenTelemetry Collector with wget for healthcheck
#
# 기본 otel-collector-contrib 이미지는 scratch 기반으로 셸/wget/curl이 없음
# Docker Compose 환경에서 헬스체크를 위해 Alpine 기반으로 빌드
#
# 참고: Kubernetes 환경에서는 외부 livenessProbe/readinessProbe로 체크 가능하므로
# 원본 이미지(otel/opentelemetry-collector-contrib) 사용 가능
#
# 버전 선택 근거 (2026-01-31 기준):
# - v0.96.0: redaction processor가 traces만 지원 (logs/metrics 미지원)
# - v0.115.0+: redaction processor가 traces, logs, metrics 모두 지원
# - v0.144.0: 현재 최신 안정 버전 (Docker Hub에서 검증됨)
# 결론: v0.144.0 - 최신 안정 버전으로 완전한 redaction 지원

ARG OTEL_VERSION=0.144.0

# Stage 1: 원본 이미지에서 바이너리 복사
FROM otel/opentelemetry-collector-contrib:${OTEL_VERSION} AS collector

# Stage 2: Alpine 기반 이미지에 바이너리 복사
FROM alpine:3.19

# 필수 패키지 설치 (wget은 busybox에 포함, ca-certificates는 TLS 연결용)
RUN apk add --no-cache ca-certificates wget

# 바이너리 복사
COPY --from=collector /otelcol-contrib /otelcol-contrib

# file_storage 익스텐션용 버퍼 디렉토리 및 WAL 디렉토리 생성
RUN mkdir -p /var/lib/otelcol/buffer/compaction /var/lib/otelcol/wal

# 비root 사용자로 실행 (보안)
RUN adduser -D -u 10001 otel && \
    chown -R otel:otel /var/lib/otelcol
USER otel

# 헬스체크 포트
EXPOSE 4317 4318 8888 13133

ENTRYPOINT ["/otelcol-contrib"]
CMD ["--config", "/etc/otelcol/otel-collector-config.yml"]
