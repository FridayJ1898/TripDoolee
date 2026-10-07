# TripDoolee - Agent Memory & Workflow Guidelines

## 📸 사진 자동 업로드 & 선별 워크플로우 (중요)
- **원본 사진 폴더**: `raw_photos/` (Git 제외 대상)
- **웹용 사진 저장 위치**: `images/photos/day{N}/`
- **사진 메타데이터**: `photos_data.js` 및 `photos_data.json`
- **핵심 파이프라인**: `process_photos.py`
  - EXIF DateTimeOriginal 기반 Day 1~11 자동 매핑
  - OpenCV Laplacian 블러 검출
  - dHash 기반 연사 중복 제거 & 베스트 샷 선별
  - 1600px 메인 이미지 + 480px 썸네일 변환
  - HEIC(아이폰) 및 JPG/PNG 완전 지원
- **실행 배치 파일**: `run_photo_processor.bat`
- **사용자 가이드**: `PHOTO_GUIDE.md` 참조

## 📖 일기 및 일정 데이터 규칙
- 날짜별 실제 일정: 9/30(Day 1) ~ 10/10(Day 11)
- 여행 일기 데이터: `defaultDiaryDay1` ~ `defaultDiaryDay11`
  - 필수 키: `day`, `dateStr`, `title`, `subTitle`, `author`, `mood`, `tags`, `stats`, `highlights`, `timelineEvents`, `paragraphs`, `quickExpenses`, `familyComments`
  - null-safety: 배열 map 호출 시 반드시 `(arr || []).map(...)` 패턴 유지
- UI: `index.html` 내 `tab-schedule`, `tab-diary`, `tab-photos`, `tab-budget` 등 탭 구조
