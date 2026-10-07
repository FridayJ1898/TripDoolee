@echo off
chcp 65001 > nul
echo ========================================================
echo   [TripDoolee] 둘리 가족 여행 사진 자동 선별 및 업로드 정리기
echo ========================================================
echo.
echo 1. raw_photos 폴더에 스마트폰 사진들을 넣어주세요.
echo 2. AI가 자동으로 흔들림/중복 사진을 걸러내고 베스트 샷을 선별합니다.
echo 3. 웹용 고화질 리사이징 및 앨범 데이터를 생성합니다.
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [알림] 가상환경을 준비합니다...
    uv venv .venv
    uv pip install --python .venv Pillow opencv-python-headless imagehash pillow-heif piexif
)

echo [진행] 사진 분석 및 최적화를 시작합니다...
.venv\Scripts\python.exe process_photos.py

echo.
echo ========================================================
echo 작업이 완료되었습니다! 
echo 이제 사이트(index.html)에서 사진첩과 일기를 확인하세요.
echo ========================================================
pause
