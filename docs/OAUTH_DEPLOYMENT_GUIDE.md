# 🔐 OAuth 배포 설정 가이드

Django 쇼핑몰 프로젝트의 Google, Kakao, Naver OAuth 실제 배포 설정 가이드입니다.

## 📋 목차

1. [Google OAuth 설정](#1-google-oauth-설정)
2. [Kakao OAuth 설정](#2-kakao-oauth-설정)
3. [Naver OAuth 설정](#3-naver-oauth-설정)
4. [환경변수 설정](#4-환경변수-설정)
5. [프로덕션 체크리스트](#5-프로덕션-체크리스트)

---

## 1. Google OAuth 설정

### 1.1 Google Cloud Console 접속

1. [Google Cloud Console](https://console.cloud.google.com/) 접속
2. 새 프로젝트 생성 또는 기존 프로젝트 선택

### 1.2 OAuth 동의 화면 구성

1. **APIs & Services** → **OAuth consent screen** 클릭
2. **User Type** 선택:
   - **External**: 모든 Google 계정 사용자 (일반 서비스용)
   - **Internal**: G Suite 조직 내 사용자만 (기업 내부용)

3. **앱 정보 입력**:
   ```
   앱 이름: [서비스명] (예: Django 쇼핑몰)
   사용자 지원 이메일: support@yourdomain.com
   앱 로고: (선택) 120x120 PNG 또는 JPEG
   ```

4. **앱 도메인 정보**:
   ```
   애플리케이션 홈페이지: https://yourdomain.com
   애플리케이션 개인정보처리방침 링크: https://yourdomain.com/privacy
   애플리케이션 서비스 약관 링크: https://yourdomain.com/terms
   ```

5. **승인된 도메인** 추가:
   ```
   yourdomain.com
   ```

6. **개발자 연락처 정보**:
   ```
   이메일: developer@yourdomain.com
   ```

### 1.3 스코프 설정

필요한 최소 스코프:
```
.../auth/userinfo.email
.../auth/userinfo.profile
openid
```

### 1.4 OAuth 클라이언트 ID 생성

1. **APIs & Services** → **Credentials** 클릭
2. **+ CREATE CREDENTIALS** → **OAuth client ID** 선택
3. **Application type**: `Web application` 선택
4. **Name**: `Django Shopping Mall` (식별용)

5. **Authorized JavaScript origins** (프론트엔드 도메인):
   ```
   # 프로덕션
   https://yourdomain.com

   # 개발용 (선택)
   http://localhost:3000
   http://localhost:8000
   ```

6. **Authorized redirect URIs** (콜백 URL):
   ```
   # 프로덕션 - 프론트엔드 콜백
   https://yourdomain.com/auth/callback

   # 프로덕션 - 백엔드 직접 콜백 (선택)
   https://api.yourdomain.com/api/auth/social/google/callback/

   # 개발용 (선택)
   http://localhost:3000/auth/callback
   http://localhost:8000/social/test/
   ```

7. **CREATE** 클릭 후 **Client ID**와 **Client Secret** 저장

### 1.5 프로덕션 앱 검증 (선택)

- **100명 이상의 사용자**를 대상으로 서비스하거나
- **민감한 스코프**를 사용하는 경우

Google의 앱 검증 절차가 필요합니다:
1. OAuth consent screen → **PUBLISH APP** 클릭
2. 검증 요청 제출 (1-4주 소요)

---

## 2. Kakao OAuth 설정

### 2.1 Kakao Developers 접속

1. [Kakao Developers](https://developers.kakao.com/) 접속
2. 로그인 후 **내 애플리케이션** 클릭

### 2.2 애플리케이션 생성

1. **애플리케이션 추가하기** 클릭
2. 앱 정보 입력:
   ```
   앱 이름: Django 쇼핑몰
   사업자명: [회사명 또는 개인명]
   ```

### 2.3 플랫폼 등록

1. **앱 설정** → **플랫폼** 클릭
2. **Web** 플랫폼 추가:
   ```
   사이트 도메인:
   - https://yourdomain.com
   - http://localhost:3000 (개발용)
   - http://localhost:8000 (개발용)
   ```

### 2.4 카카오 로그인 활성화

1. **제품 설정** → **카카오 로그인** 클릭
2. **활성화 설정**: `ON`
3. **Redirect URI** 등록:
   ```
   # 프론트엔드 콜백
   https://yourdomain.com/auth/callback

   # 백엔드 직접 콜백 (선택)
   https://api.yourdomain.com/api/auth/social/kakao/callback/

   # 개발용
   http://localhost:3000/auth/callback
   http://localhost:8000/social/test/
   ```

### 2.5 동의항목 설정

1. **카카오 로그인** → **동의항목** 클릭
2. 필수 항목 설정:

   | 항목 | 동의 단계 | 필수 여부 |
   |------|----------|----------|
   | 닉네임 | 필수 동의 | ✅ |
   | 프로필 사진 | 선택 동의 | |
   | 카카오계정(이메일) | 필수 동의 | ✅ |

### 2.6 앱 키 확인

1. **앱 설정** → **앱 키** 클릭
2. 필요한 키:
   - **REST API 키**: `KAKAO_REST_API_KEY`로 사용
   - **Client Secret**: 보안 → 코드 생성 (아래 참고)

### 2.7 Client Secret 생성 (권장)

1. **제품 설정** → **카카오 로그인** → **보안** 클릭
2. **Client Secret**: `코드 생성` 클릭
3. **활성화 상태**: `사용함` 선택

---

## 3. Naver OAuth 설정

### 3.1 Naver Developers 접속

1. [Naver Developers](https://developers.naver.com/) 접속
2. 로그인 후 **Application** → **애플리케이션 등록** 클릭

### 3.2 애플리케이션 등록

1. **애플리케이션 이름**: `Django 쇼핑몰`
2. **사용 API**: `네아로(네이버 아이디로 로그인)` 선택

3. **제공 정보 선택** (필수):
   - ✅ 회원이름 (필수)
   - ✅ 이메일 주소 (필수)
   - ☑️ 프로필 사진 (선택)
   - ☑️ 별명 (선택)

4. **로그인 오픈 API 서비스 환경**:
   - **서비스 URL**: `https://yourdomain.com`
   - **네아로 Callback URL**:
     ```
     # 프론트엔드 콜백
     https://yourdomain.com/auth/callback

     # 백엔드 직접 콜백 (선택)
     https://api.yourdomain.com/api/auth/social/naver/callback/

     # 개발용
     http://localhost:3000/auth/callback
     http://localhost:8000/social/test/
     ```

### 3.3 앱 키 확인

등록 완료 후 **내 애플리케이션**에서:
- **Client ID**: `NAVER_CLIENT_ID`
- **Client Secret**: `NAVER_CLIENT_SECRET`

### 3.4 검수 요청 (프로덕션 필수)

개발 상태에서는 **본인 계정만** 로그인 가능합니다.

1. **검수 요청** 탭 클릭
2. 필요 정보 입력:
   - 서비스 소개
   - 서비스 화면 스크린샷
   - 로그인 후 사용 화면
3. 검수 승인 (보통 1-3일 소요)

---

## 4. 환경변수 설정

### 4.1 프로덕션 환경변수 (.env.production)

```bash
# ==========================================
# Google OAuth
# ==========================================
GOOGLE_CLIENT_ID=123456789012-xxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxxxxxx

# ==========================================
# Kakao OAuth
# ==========================================
KAKAO_REST_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
KAKAO_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ==========================================
# Naver OAuth
# ==========================================
NAVER_CLIENT_ID=xxxxxxxxxxxx
NAVER_CLIENT_SECRET=xxxxxxxxxx

# ==========================================
# OAuth Redirect URI
# ==========================================
# 프론트엔드가 처리하는 경우 (SPA 방식 - 권장)
SOCIAL_LOGIN_REDIRECT_URI=https://yourdomain.com/auth/callback

# 백엔드가 직접 처리하는 경우 (서버 렌더링 방식)
# SOCIAL_LOGIN_REDIRECT_URI=https://api.yourdomain.com/api/auth/callback/

# ==========================================
# 추가 보안 설정
# ==========================================
CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://api.yourdomain.com
```

### 4.2 프로덕션 Django 설정 수정

`myproject/settings/production.py`에 추가:

```python
# REST Auth - Production 보안 설정
REST_AUTH["JWT_AUTH_SECURE"] = True  # HTTPS 필수

# 프로덕션 HTTPS 강제
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
```

---

## 5. 프로덕션 체크리스트

### 5.1 사전 준비

- [ ] 도메인 SSL 인증서 설치 (HTTPS 필수)
- [ ] 개인정보처리방침 페이지 준비
- [ ] 서비스 이용약관 페이지 준비

### 5.2 Google OAuth

- [ ] OAuth 동의 화면 완성
- [ ] 프로덕션 도메인 승인된 도메인에 추가
- [ ] Redirect URI에 프로덕션 URL 추가
- [ ] 테스트 사용자 추가 (검증 전)
- [ ] (필요시) 앱 검증 요청

### 5.3 Kakao OAuth

- [ ] 플랫폼에 프로덕션 도메인 추가
- [ ] Redirect URI 프로덕션 URL 추가
- [ ] 동의항목 이메일 필수 설정
- [ ] Client Secret 생성 및 활성화

### 5.4 Naver OAuth

- [ ] 서비스 URL 프로덕션 도메인 설정
- [ ] Callback URL 프로덕션 URL 추가
- [ ] 검수 요청 및 승인

### 5.5 Django 설정

- [ ] 환경변수 프로덕션 값으로 설정
- [ ] `DEBUG = False` 확인
- [ ] `SECURE_SSL_REDIRECT = True` 확인
- [ ] `REST_AUTH["JWT_AUTH_SECURE"] = True` 확인
- [ ] `CSRF_TRUSTED_ORIGINS` 프로덕션 도메인 설정

### 5.6 테스트

- [ ] Google 로그인 테스트
- [ ] Kakao 로그인 테스트
- [ ] Naver 로그인 테스트
- [ ] JWT 토큰 발급 확인
- [ ] 사용자 정보 정상 저장 확인

---

## 🔧 트러블슈팅

### 일반적인 오류

#### 1. `redirect_uri_mismatch` 오류
```
Error 400: redirect_uri_mismatch
```
**원인**: 등록된 Redirect URI와 실제 요청 URI가 다름
**해결**:
- 콘솔에서 등록한 URI와 정확히 일치하는지 확인
- 프로토콜(http/https), 포트, 경로 끝 슬래시 확인

#### 2. `invalid_client` 오류
```
{"error": "invalid_client"}
```
**원인**: Client ID 또는 Secret이 잘못됨
**해결**:
- 환경변수 값 재확인
- 공백, 줄바꿈 문자 제거

#### 3. Kakao `KOE006` 오류
```
{"error": "invalid_scope"}
```
**원인**: 동의항목 설정 누락
**해결**: 카카오 개발자 콘솔에서 이메일 동의항목 설정

#### 4. Naver `024` 오류
```
인증에 문제가 발생했습니다.
```
**원인**: 검수 미완료 상태에서 타 사용자 로그인 시도
**해결**: 검수 요청 후 승인 대기

---

## 📚 참고 자료

- [django-allauth 공식 문서](https://django-allauth.readthedocs.io/)
- [dj-rest-auth 공식 문서](https://dj-rest-auth.readthedocs.io/)
- [Google OAuth 2.0 가이드](https://developers.google.com/identity/protocols/oauth2)
- [Kakao 로그인 가이드](https://developers.kakao.com/docs/latest/ko/kakaologin/common)
- [Naver 로그인 가이드](https://developers.naver.com/docs/login/overview/)

---

## 🔒 보안 권장사항

1. **Client Secret은 절대 클라이언트(프론트엔드) 코드에 노출하지 마세요**
2. **환경변수 파일(.env)을 Git에 커밋하지 마세요**
3. **프로덕션에서는 반드시 HTTPS를 사용하세요**
4. **주기적으로 Client Secret을 교체하세요**
5. **불필요한 스코프는 요청하지 마세요**
