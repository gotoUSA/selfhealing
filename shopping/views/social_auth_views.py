"""
소셜 로그인 OAuth 콜백 처리 뷰

각 OAuth 제공자로부터 authorization code를 받아
access token으로 교환하고 JWT 토큰을 발급합니다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponseRedirect
from django.views import View
from rest_framework_simplejwt.tokens import RefreshToken

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)
User = get_user_model()


class SocialCallbackView(View):
    """
    OAuth 콜백 통합 처리 뷰
    
    Google, Kakao, Naver의 OAuth 콜백을 처리하고
    JWT 토큰을 발급하여 프론트엔드로 리다이렉트합니다.
    """
    
    # OAuth 제공자별 설정
    PROVIDERS = {
        'google': {
            'token_url': 'https://oauth2.googleapis.com/token',
            'userinfo_url': 'https://www.googleapis.com/oauth2/v2/userinfo',
            'client_id_env': 'GOOGLE_CLIENT_ID',
            'client_secret_env': 'GOOGLE_CLIENT_SECRET',
        },
        'kakao': {
            'token_url': 'https://kauth.kakao.com/oauth/token',
            'userinfo_url': 'https://kapi.kakao.com/v2/user/me',
            'client_id_env': 'KAKAO_REST_API_KEY',
            'client_secret_env': 'KAKAO_CLIENT_SECRET',
        },
        'naver': {
            'token_url': 'https://nid.naver.com/oauth2.0/token',
            'userinfo_url': 'https://openapi.naver.com/v1/nid/me',
            'client_id_env': 'NAVER_CLIENT_ID',
            'client_secret_env': 'NAVER_CLIENT_SECRET',
        },
    }
    
    def get(self, request):
        """OAuth 콜백 처리"""
        code = request.GET.get('code')
        state = request.GET.get('state', '')
        error = request.GET.get('error')
        
        # 에러 체크
        if error:
            error_desc = request.GET.get('error_description', error)
            return self._redirect_with_error(error_desc)
        
        if not code:
            return self._redirect_with_error('Authorization code가 없습니다.')
        
        # state에서 provider 추출 (format: provider_randomstring)
        provider = state.split('_')[0] if '_' in state else None
        
        if provider not in self.PROVIDERS:
            return self._redirect_with_error(f'지원하지 않는 OAuth 제공자: {provider}')
        
        try:
            # 1. Authorization code를 access token으로 교환
            token_data = self._exchange_code_for_token(provider, code, request)
            
            if not token_data or 'access_token' not in token_data:
                error_detail = token_data.get('error_description') if token_data else '응답 없음'
                return self._redirect_with_error(f'토큰 교환 실패: {error_detail}')
            
            # 2. 사용자 정보 가져오기
            user_info = self._get_user_info(provider, token_data['access_token'])
            
            if not user_info:
                return self._redirect_with_error('사용자 정보 조회 실패')
            
            # 3. 사용자 생성 또는 조회
            user = self._get_or_create_user(provider, user_info)
            
            # 4. JWT 토큰 발급
            refresh = RefreshToken.for_user(user)
            access_token = str(refresh.access_token)
            refresh_token = str(refresh)
            
            # 5. 프론트엔드로 리다이렉트 (토큰 포함)
            return self._redirect_with_tokens(access_token, refresh_token)
            
        except Exception as e:
            logger.exception(f'소셜 로그인 처리 중 오류: {e}')
            return self._redirect_with_error(str(e))
    
    def _exchange_code_for_token(self, provider: str, code: str, request) -> dict | None:
        """Authorization code를 access token으로 교환"""
        import os
        
        config = self.PROVIDERS[provider]
        
        # redirect_uri는 OAuth 콘솔에 등록한 것과 정확히 일치해야 함
        # request.build_absolute_uri()가 프록시 환경에서 다른 값을 반환할 수 있음
        redirect_uri = 'http://localhost:8000/api/social/callback/'
        
        client_id = os.getenv(config['client_id_env'])
        client_secret = os.getenv(config['client_secret_env'])
        
        logger.info(f'{provider} 토큰 교환 시도 - redirect_uri: {redirect_uri}')
        logger.info(f'{provider} client_id 존재: {bool(client_id)}, client_secret 존재: {bool(client_secret)}')
        
        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': client_id,
            'client_secret': client_secret,
        }
        
        try:
            response = requests.post(
                config['token_url'],
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=10
            )
            
            logger.info(f'{provider} 토큰 응답: {response.status_code}')
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f'{provider} 토큰 교환 실패: {response.status_code} - {response.text}')
                return None
                
        except requests.RequestException as e:
            logger.error(f'{provider} 토큰 요청 오류: {e}')
            return None
    
    def _get_user_info(self, provider: str, access_token: str) -> dict | None:
        """OAuth 제공자로부터 사용자 정보 조회"""
        config = self.PROVIDERS[provider]
        
        headers = {'Authorization': f'Bearer {access_token}'}
        
        try:
            response = requests.get(
                config['userinfo_url'],
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return self._normalize_user_info(provider, data)
            else:
                logger.error(f'{provider} 사용자 정보 조회 실패: {response.status_code}')
                return None
                
        except requests.RequestException as e:
            logger.error(f'{provider} 사용자 정보 요청 오류: {e}')
            return None
    
    def _normalize_user_info(self, provider: str, data: dict) -> dict:
        """각 제공자별 응답을 통일된 형식으로 변환"""
        if provider == 'google':
            return {
                'email': data.get('email'),
                'name': data.get('name'),
                'provider_id': data.get('id'),
                'profile_image': data.get('picture'),
            }
        elif provider == 'kakao':
            kakao_account = data.get('kakao_account', {})
            profile = kakao_account.get('profile', {})
            return {
                'email': kakao_account.get('email'),
                'name': profile.get('nickname'),
                'provider_id': str(data.get('id')),
                'profile_image': profile.get('profile_image_url'),
            }
        elif provider == 'naver':
            response = data.get('response', {})
            return {
                'email': response.get('email'),
                'name': response.get('name') or response.get('nickname'),
                'provider_id': response.get('id'),
                'profile_image': response.get('profile_image'),
            }
        return {}
    
    def _get_or_create_user(self, provider: str, user_info: dict):
        """사용자 조회 또는 생성"""
        email = user_info.get('email')
        provider_id = user_info.get('provider_id')
        
        # 이메일이 없는 경우 (카카오 비즈니스 심사 전 등)
        if not email:
            if provider_id:
                email = f'{provider}_{provider_id}@social.local'
                logger.info(f'{provider} 이메일 없음, 대체 이메일 생성: {email}')
            else:
                raise ValueError('이메일 정보가 없습니다. OAuth 제공자 설정에서 이메일 권한을 확인하세요.')
        
        # 기존 사용자 확인
        try:
            user = User.objects.get(email=email)
            logger.info(f'기존 사용자 로그인: {email} via {provider}')
        except User.DoesNotExist:
            # 새 사용자 생성
            username = self._generate_username(email, provider, provider_id)
            user = User.objects.create_user(
                username=username,
                email=email,
                is_email_verified=True,  # 소셜 로그인은 이메일 인증 완료
            )
            
            # 이름 설정 (있으면)
            if user_info.get('name'):
                user.first_name = user_info['name']
                user.save(update_fields=['first_name'])
            
            logger.info(f'새 사용자 생성: {email} via {provider}')
        
        return user
    
    def _generate_username(self, email: str, provider: str, provider_id: str) -> str:
        """고유한 username 생성"""
        base_username = email.split('@')[0]
        username = f'{base_username}_{provider}'
        
        # 중복 체크
        counter = 1
        original_username = username
        while User.objects.filter(username=username).exists():
            username = f'{original_username}_{counter}'
            counter += 1
        
        return username
    
    def _redirect_with_tokens(self, access_token: str, refresh_token: str) -> HttpResponseRedirect:
        """토큰과 함께 프론트엔드로 리다이렉트"""
        # 테스트 페이지로 리다이렉트 (프론트엔드 URL로 변경 가능)
        redirect_url = '/api/social/test/'
        params = urlencode({
            'access_token': access_token,
            'refresh_token': refresh_token,
        })
        return HttpResponseRedirect(f'{redirect_url}?{params}')
    
    def _redirect_with_error(self, error_message: str) -> HttpResponseRedirect:
        """에러와 함께 프론트엔드로 리다이렉트"""
        redirect_url = '/api/social/test/'
        params = urlencode({
            'error': 'oauth_error',
            'error_description': error_message,
        })
        return HttpResponseRedirect(f'{redirect_url}?{params}')
