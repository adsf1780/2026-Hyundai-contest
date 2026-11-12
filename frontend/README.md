# 이어톡 Android 앱

Android Studio에서 이 디렉터리를 프로젝트로 엽니다. Java 17 이상, Android SDK Platform 36, Build Tools 35.0.0, 동봉된 Gradle 8.12 래퍼를 사용합니다. 앱의 최소 API는 28이고 Pleos Connect 2.1.0 에뮬레이터의 실행 API는 34입니다.

```powershell
.\gradlew.bat assembleDebug
adb install -r build/outputs/apk/debug/EarTalk-debug.apk
adb shell am start -n kr.eartalk.car/.MainActivity
```

## 서버 연결

기존 백엔드에 PostgreSQL 마이그레이션과 콘텐츠 시드를 적용하고, 기기 계정을 발급합니다. 아래 명령은 백엔드 의존성과 `DATABASE_URL`이 설정된 저장소 루트에서 실행합니다.

```powershell
alembic upgrade head
python -m app.cli seed
python -m app.cli account --name "운전자"
python -m uvicorn app.main:app --port 8000
```

앱 시작 화면에 서버 주소와 계정 발급 결과의 `access_token`을 입력합니다. 디버그 APK에서 에뮬레이터용 기본 주소는 `http://10.0.2.2:8000`입니다. 배포용 서버에는 HTTPS가 필요합니다. 토큰과 전송 대기 답변은 Android Keystore 키로 암호화해 앱 내부에 저장합니다.

AI 피드백을 받으려면 서버 측에서 모델 설정을 마치고 별도 터미널에 `python -m app.worker`를 실행합니다. 평가 중인 답변은 서버의 세션 상태를 다시 조회해 반영합니다.

## 차량에서 확인하기

[공식 에뮬레이터 설치 가이드](https://document.pleos.ai/docs/connect/guide/getting-started/application-development/setup-connect-sdk-emulator)에 따라 SDK Manager에서 Windows용 업데이트 사이트를 등록하고, 약관 확인 후 API 34의 Pleos Connect 시스템 이미지를 설치합니다. Device Manager에서 Automotive → Pleos Connect v2.1.0을 선택합니다.

차량에서는 Pleos TextToSpeech와 SpeechToText 3.3.1의 온디바이스 모드를 사용합니다. 안내는 한국어, 예문과 답변 인식은 영어입니다. 음성 답변을 시험하려면 [Pleos 마이크 설정 안내](https://document.pleos.ai/docs/connect/guide/getting-started/application-development/configure-pleos-connect-emulator)에 따라 에뮬레이터의 마이크 입력을 켜고 수업에서 **영어로 답하기**를 누릅니다. 일반 Android 기기에서는 기기의 TTS와 음성 인식 서비스를 사용합니다.

차량에서는 Android Automotive의 주행 중 사용자 인터페이스 제한을 구독합니다. 제한 상태에서는 수업과 입력 화면을 닫고 음성 입출력을 멈춥니다. 주차 상태로 돌아온 뒤 사용자가 다시 수업을 선택합니다. Gleo 음성 호출은 후속 연동 항목입니다.

