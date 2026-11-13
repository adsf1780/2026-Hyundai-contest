# 이어톡

이어톡은 실시간 주행 상황에 맞춰 학습 내용을 제공하고, 주행 중 학습이 중단되더라도 자연스럽게 이어갈 수 있도록 돕는 **Pleos 기반 맞춤형 음성 영어 학습 앱**입니다.

아래 실행 방법은 **Windows PowerShell에서 저장소 루트 폴더**를 기준으로 합니다. AI 평가는 COPA API(`copa.codyssey.kr`)를 사용합니다.

## 테스트 환경 및 필요한 설정

- Windows에서 Python, Docker Desktop, Android Studio를 사용하며, DB는 PostgreSQL 17입니다.
- 개발에 사용한 에뮬레이터는 **Pleos Connect v2.1.0 (Android API 34, x86_64)**이며, 화면은 **2560 × 1440 px, 160 dpi, 가로 방향**입니다.
- 앱 빌드에는 Java 17 이상, Android SDK Platform 36, Build Tools 35.0.0이 필요합니다. Gradle은 프로젝트에 포함된 래퍼를 사용합니다.
- Docker Desktop과 에뮬레이터를 실행하고, 음성 학습을 위해 에뮬레이터의 마이크 입력을 켭니다. 학습 동작은 주차 상태에서 확인합니다.
- 아래 안내에 따라 DB 연결 정보, 기기 토큰, COPA API 키와 모델 ID를 설정하고 백엔드와 AI 평가 워커를 함께 실행합니다.

[Pleos 설치 안내](https://document.pleos.ai/docs/connect/guide/getting-started/application-development/setup-connect-sdk-emulator) · [마이크 설정 안내](https://document.pleos.ai/docs/connect/guide/getting-started/application-development/configure-pleos-connect-emulator)

## 제출 APK

제출용으로 서명한 release APK: [뭐시기뭐시기_이어톡_release.apk](apk/뭐시기뭐시기_이어톡_release.apk)

아래 명령으로 설치한 뒤, 앱에 HTTPS 서버 주소와 발급받은 기기 토큰을 입력합니다. 로컬 HTTP 서버로 개발·테스트할 때는 아래의 debug APK 실행 방법을 사용합니다.

```powershell
adb -e install -r ".\apk\뭐시기뭐시기_이어톡_release.apk"
```

## 1. 최초 준비

### Python 환경과 DB 생성

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 로컬 개발용 예시 비밀번호. 두 설정의 비밀번호를 동일하게 맞춥니다.
$env:DB_PASSWORD = "eartalk_local_2026"
$env:DATABASE_URL = "postgresql+psycopg://eartalk:eartalk_local_2026@127.0.0.1:15432/eartalk"

docker compose up -d --wait
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.cli seed
.\.venv\Scripts\python.exe -m app.cli account --name "운전자" --hours 720
```

마지막 출력의 **`access_token` 값만 복사**해 둡니다. 유효기간은 30일이며 사용자마다 계정을 발급합니다. 같은 토큰을 공유하면 학습 기록과 진행 중인 수업도 공유됩니다.

DB 데이터는 Docker의 `pgdata` 볼륨에 보관됩니다. 이후 실행 시 기존 데이터를 사용합니다. 환경변수는 현재 PowerShell 창에서만 유지됩니다.

### 개발용 APK 빌드와 설치

```powershell
.\frontend\gradlew.bat -p frontend assembleDebug
adb -e install -r .\frontend\build\outputs\apk\debug\EarTalk-debug.apk
```

개발용 APK가 준비되어 있다면 설치 명령부터 실행합니다. 앱 변경 후에도 위 명령으로 다시 빌드하고 설치합니다. `adb -e`는 에뮬레이터가 하나 실행 중인 경우를 기준으로 합니다. 제출용 release APK는 HTTPS 서버 주소로 연결해야 합니다.

## 2. 백엔드 실행

새 PowerShell 창을 열 때 아래 설정부터 실행합니다. 최초 준비와 같은 창이라면 마지막 서버 실행 명령만 실행해도 됩니다.

```powershell
$env:DB_PASSWORD = "eartalk_local_2026"
$env:DATABASE_URL = "postgresql+psycopg://eartalk:eartalk_local_2026@127.0.0.1:15432/eartalk"

docker compose up -d --wait
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

이 창을 열어 둡니다. PC에서 [서버 상태](http://127.0.0.1:8000/health)를 열어 `{"status":"ok"}` 응답을 확인합니다. [API 문서](http://127.0.0.1:8000/docs)도 확인할 수 있습니다.

## 3. AI 평가 워커 실행

**별도 PowerShell 창을 저장소 루트에서 열고** 실행합니다. 발급받은 COPA API 키와 해당 API에서 사용할 수 있는 모델 ID를 입력합니다.

```powershell
$env:DATABASE_URL = "postgresql+psycopg://eartalk:eartalk_local_2026@127.0.0.1:15432/eartalk"
$env:API_KEY = "발급받은 COPA API 키"
$env:MODEL = "사용할 모델 ID"

.\.venv\Scripts\python.exe -m app.worker
```

이 창도 열어 둡니다. 백엔드와 워커는 같은 `DATABASE_URL`을 사용해야 하며, 워커가 실행되어야 제출한 영어 답변의 AI 평가가 처리됩니다.

최신 `main`의 워커는 `API_KEY`와 `MODEL` 환경변수를 사용합니다. 두 값은 워커를 실행하는 PowerShell 창에 설정해야 하며, 새 창을 열면 다시 설정합니다. API 키는 로컬 환경변수로만 설정합니다.

## 4. 앱 실행과 연결

Pleos 홈의 앱 목록에서 **이어톡**을 선택하거나 아래 명령으로 실행합니다.

```powershell
adb -e shell am start --user current -n kr.eartalk.car/.MainActivity
```

| 앱 입력칸 | 입력할 값 |
|---|---|
| 서버 주소 | `http://10.0.2.2:8000` |
| 기기 토큰 | 계정 생성 결과의 `access_token` 값 |

에뮬레이터에서 `10.0.2.2`는 서버를 실행한 PC를 가리킵니다. **연결하기**를 누르고 수업을 시작합니다. 음성 학습은 에뮬레이터의 마이크 입력을 켜고 주차 상태에서 확인합니다.

붙여넣기가 안 되면 PC에서 토큰 값만 복사하고, 앱의 **기기 토큰 입력칸을 클릭한 후** PowerShell에서 실행합니다.

```powershell
$deviceToken = (Get-Clipboard -Raw).Trim()
if ($deviceToken -match '^[A-Za-z0-9_-]+$') {
    adb -e shell input text $deviceToken
}
```

## 종료

백엔드와 AI 워커 창에서 각각 `Ctrl+C`를 누릅니다. DB도 멈추려면 환경변수를 설정한 터미널에서 `docker compose stop`을 실행합니다. 다음 실행 시 `docker compose up -d --wait`로 다시 시작합니다.
