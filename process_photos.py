"""
TripDoolee - 사진 자동 선별, 리사이징, 일기 매칭 및 웹 앨범 생성 파이프라인
---------------------------------------------------------------------------------
기능:
1. raw_photos/ 폴더 내 스마트폰 사진(JPG, PNG, HEIC 등) 자동 검색
2. EXIF 촬영 일시 분석하여 Day 1 ~ Day 11 자동 매핑
3. OpenCV Laplacian 기반 흔들림(블러) 점수 측정
4. dHash 기반 연속 연사 / 중복 사진 클러스터링 및 베스트 샷 자동 선별
5. 웹용 최적화(긴 축 1600px) 및 초고속 썸네일(긴 축 480px) 자동 리사이징 & 압축
6. photos_data.js 및 photos_data.json 자동 생성 (사이트와 100% 실시간 연동)
"""

import os
import sys
import json
import shutil
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# Windows 콘솔 UTF-8 이모지 및 한글 인코딩 보장
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# PIL 및 Pillow-HEIF
from PIL import Image, ImageOps
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

# OpenCV 및 ImageHash
import cv2
import numpy as np
import imagehash

# 여행 일정 날짜 정의 (시애틀 현지 시각 기준)
TRIP_DATES = {
    "2026-09-30": 1,
    "2026-10-01": 2,
    "2026-10-02": 3,
    "2026-10-03": 4,
    "2026-10-04": 5,
    "2026-10-05": 6,
    "2026-10-06": 7,
    "2026-10-07": 8,
    "2026-10-08": 9,
    "2026-10-09": 10,
    "2026-10-10": 11,
}

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff"}

def get_exif_datetime(image_path: Path):
    """이미지 EXIF에서 촬영 일시 추출. 실패 시 파일 수정 시각 반환."""
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if exif:
                # 36867: DateTimeOriginal, 36868: DateTimeDigitized, 306: DateTime
                for tag_id in [36867, 36868, 306]:
                    val = exif.get(tag_id)
                    if val and isinstance(val, str):
                        try:
                            # 형식: "YYYY:MM:DD HH:MM:SS"
                            return datetime.strptime(val[:19], "%Y:%m:%d %H:%M:%S")
                        except Exception:
                            pass
    except Exception:
        pass
    
    # EXIF가 없거나 파싱 실패 시 파일 수정 시각 활용
    mtime = os.path.getmtime(image_path)
    return datetime.fromtimestamp(mtime)

def calculate_sharpness(pil_img: Image.Image) -> float:
    """Laplacian 연산으로 이미지 선명도(블러 여부) 점수 계산."""
    try:
        # 연산 속도를 위해 그레이스케일 축소 이미지로 계산
        gray = ImageOps.grayscale(pil_img)
        w, h = gray.size
        if max(w, h) > 800:
            scale = 800.0 / max(w, h)
            gray = gray.resize((int(w * scale), int(h * scale)), Image.Resampling.BILINEAR)
        img_np = np.array(gray)
        laplacian_var = cv2.Laplacian(img_np, cv2.CV_64F).var()
        return float(laplacian_var)
    except Exception as e:
        return 50.0

def calculate_dhash(pil_img: Image.Image):
    """이미지 중복 비교용 dHash 계산."""
    try:
        return imagehash.dhash(pil_img)
    except Exception:
        return None

def determine_day(dt: datetime, rel_path: Path, timezone_mode: str = "auto") -> int:
    """
    폴더명이나 촬영 일시를 기준으로 여행 Day 번호(1~11)를 판정.
    """
    # 1. 상위 폴더 이름에 day1, day2 등이 명시되어 있으면 최우선 적용
    for part in rel_path.parts:
        part_lower = part.lower().strip()
        if part_lower.startswith("day"):
            num_part = ''.join(filter(str.isdigit, part_lower))
            if num_part and 1 <= int(num_part) <= 11:
                return int(num_part)
    
    # 2. 날짜 기반 매핑
    date_str = dt.strftime("%Y-%m-%d")
    if date_str in TRIP_DATES:
        return TRIP_DATES[date_str]
    
    # 만약 카메라가 한국 시간(KST, UTC+9)으로 기록된 경우 (시애틀보다 16시간 빠름)
    # 한국 날짜에서 16시간을 빼면 시애틀 현지 날짜가 됨
    if timezone_mode in ["kst", "auto"]:
        dt_pdt = dt - timedelta(hours=16)
        date_str_pdt = dt_pdt.strftime("%Y-%m-%d")
        if date_str_pdt in TRIP_DATES:
            return TRIP_DATES[date_str_pdt]

    # 기본값: 날짜가 가장 가까운 Day를 추정하거나 1일차
    return 1

def process_photos(raw_dir: str = "raw_photos",
                   output_dir: str = "images/photos",
                   max_per_day: int = 25,
                   all_photos: bool = False,
                   timezone_mode: str = "auto"):
    """사진 분석, 베스트 선별, 리사이징 및 메타데이터 생성."""
    raw_path = Path(raw_dir)
    out_path = Path(output_dir)
    
    if not raw_path.exists():
        raw_path.mkdir(parents=True, exist_ok=True)
        print(f"📁 '{raw_dir}' 폴더가 생성되었습니다. 스마트폰 사진을 여기에 넣어주세요!")
        return

    # 1. 파일 검색
    files = []
    for p in raw_path.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            files.append(p)
    
    if not files:
        print(f"⚠️ '{raw_dir}' 폴더에 처리할 사진이 없습니다.")
        print(f"💡 스마트폰에서 찍은 사진들을 '{raw_dir}' 폴더 안에 복사한 후 다시 실행해주세요!")
        return

    print(f"📸 총 {len(files)}장의 원본 사진을 발견했습니다. 분석을 시작합니다...\n")

    # 2. EXIF 일시 분석 및 Day별 분류
    day_groups = {}
    for i, file_p in enumerate(files, 1):
        rel_p = file_p.relative_to(raw_path)
        dt = get_exif_datetime(file_p)
        day_num = determine_day(dt, rel_p, timezone_mode=timezone_mode)
        
        if day_num not in day_groups:
            day_groups[day_num] = []
        
        day_groups[day_num].append({
            "path": file_p,
            "datetime": dt,
            "rel_path": rel_p
        })
        sys.stdout.write(f"\r🔍 [1/3] EXIF 및 날짜 분석 중: {i}/{len(files)} 완료...")
        sys.stdout.flush()
    print("\n")

    # 3. Day별 선명도 측정, 중복/연사 클러스터링 및 베스트 선별
    selected_by_day = {}
    total_selected = 0

    for day_num in sorted(day_groups.keys()):
        items = day_groups[day_num]
        items.sort(key=lambda x: x["datetime"])
        print(f"📅 [Day {day_num}] 원본 {len(items)}장 분석 및 선별 중...")

        analyzed_items = []
        for it in items:
            p = it["path"]
            try:
                with Image.open(p) as img:
                    img_transposed = ImageOps.exif_transpose(img)
                    sharpness = calculate_sharpness(img_transposed)
                    dhash = calculate_dhash(img_transposed)
                    analyzed_items.append({
                        **it,
                        "sharpness": sharpness,
                        "dhash": dhash,
                        "width": img_transposed.width,
                        "height": img_transposed.height
                    })
            except Exception as e:
                print(f"  ⚠️ 파일 읽기 오류 ({p.name}): {e}")

        if all_photos:
            # 모든 사진 유지 옵션
            chosen = analyzed_items
        else:
            # 중복/연사 클러스터링 & 베스트 선별
            clusters = []
            curr_cluster = []

            for item in analyzed_items:
                if not curr_cluster:
                    curr_cluster.append(item)
                    continue

                prev_item = curr_cluster[-1]
                time_diff = abs((item["datetime"] - prev_item["datetime"]).total_seconds())

                # 유사도 판별 (시간 45초 이내 & 해시 거리 8 이하)
                is_similar = False
                if time_diff < 45 and item["dhash"] is not None and prev_item["dhash"] is not None:
                    hash_diff = item["dhash"] - prev_item["dhash"]
                    if hash_diff <= 8:
                        is_similar = True

                if is_similar:
                    curr_cluster.append(item)
                else:
                    clusters.append(curr_cluster)
                    curr_cluster = [item]

            if curr_cluster:
                clusters.append(curr_cluster)

            # 각 클러스터에서 가장 선명한(sharpness 최고) 1장 선정
            candidate_list = []
            for cl in clusters:
                best = max(cl, key=lambda x: x["sharpness"])
                candidate_list.append(best)

            # Day당 최대 수량(max_per_day) 제한이 있고 후보가 더 많은 경우:
            # 시간대별 균등 분포(아침~밤)를 유지하며 상위 샷 선택
            if len(candidate_list) > max_per_day:
                # 시간 순서를 유지하면서 균등 간격 샘플링 + 고선명도 가중치
                step = len(candidate_list) / max_per_day
                chosen = []
                for idx in range(max_per_day):
                    start_i = int(idx * step)
                    end_i = int((idx + 1) * step)
                    segment = candidate_list[start_i:end_i] if start_i < end_i else [candidate_list[start_i]]
                    best_in_seg = max(segment, key=lambda x: x["sharpness"])
                    chosen.append(best_in_seg)
            else:
                chosen = candidate_list

        selected_by_day[day_num] = chosen
        total_selected += len(chosen)
        print(f"  ✨ Day {day_num}: 원본 {len(items)}장 중 베스트 {len(chosen)}장 자동 선별 완료!")

    print(f"\n🌟 총 {len(files)}장의 원본 중 최종 {total_selected}장의 베스트 사진이 선별되었습니다.")
    print("🖼️ [2/3] 웹용 고화질(1600px) 및 초고속 썸네일(480px) 리사이징 진행 중...")

    # 4. 이미지 리사이징, 압축 저장 및 메타데이터 작성
    web_photos_metadata = []
    seq_counter = 0

    for day_num in sorted(selected_by_day.keys()):
        day_out_dir = out_path / f"day{day_num}"
        day_out_dir.mkdir(parents=True, exist_ok=True)
        chosen_items = selected_by_day[day_num]

        for idx, it in enumerate(chosen_items, 1):
            seq_counter += 1
            src_file = it["path"]
            filename_base = f"photo_{idx:02d}"
            main_filename = f"{filename_base}.jpg"
            thumb_filename = f"thumb_{idx:02d}.jpg"

            main_dest = day_out_dir / main_filename
            thumb_dest = day_out_dir / thumb_filename

            try:
                with Image.open(src_file) as img:
                    img_transposed = ImageOps.exif_transpose(img)
                    if img_transposed.mode in ("RGBA", "P"):
                        img_transposed = img_transposed.convert("RGB")

                    # 메인 웹 이미지 (긴 축 1600px, quality=85)
                    w, h = img_transposed.size
                    max_dim = 1600
                    if max(w, h) > max_dim:
                        scale = max_dim / float(max(w, h))
                        new_w, new_h = int(w * scale), int(h * scale)
                        main_img = img_transposed.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    else:
                        main_img = img_transposed
                        new_w, new_h = w, h

                    main_img.save(main_dest, "JPEG", quality=85, optimize=True, progressive=True)

                    # 썸네일 (긴 축 480px, quality=80)
                    thumb_max = 480
                    thumb_scale = thumb_max / float(max(w, h))
                    thumb_w, thumb_h = int(w * thumb_scale), int(h * thumb_scale)
                    thumb_img = img_transposed.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
                    thumb_img.save(thumb_dest, "JPEG", quality=80, optimize=True)

                    # 메타데이터 기록
                    time_str = it["datetime"].strftime("%H:%M")
                    date_str = it["datetime"].strftime("%Y-%m-%d")
                    web_photos_metadata.append({
                        "id": f"d{day_num}_{idx:02d}",
                        "day": day_num,
                        "date": date_str,
                        "time": time_str,
                        "src": f"images/photos/day{day_num}/{main_filename}",
                        "thumb": f"images/photos/day{day_num}/{thumb_filename}",
                        "caption": f"Day {day_num}의 순간 ({time_str})",
                        "width": new_w,
                        "height": new_h,
                        "sharpness": round(it["sharpness"], 1)
                    })

            except Exception as e:
                print(f"  ⚠️ 변환 실패 ({src_file.name}): {e}")

            sys.stdout.write(f"\r🎨 [3/3] 변환 및 웹 저장 중: {seq_counter}/{total_selected} 완료...")
            sys.stdout.flush()

    print("\n")

    # 5. photos_data.js & photos_data.json 저장
    js_content = f"""// TripDoolee 자동 생성 포토 앨범 데이터 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
// 총 {len(web_photos_metadata)}장의 엄선된 베스트 사진
window.TRIP_PHOTOS_DATA = {json.dumps(web_photos_metadata, ensure_ascii=False, indent=2)};
"""
    with open("photos_data.js", "w", encoding="utf-8") as f:
        f.write(js_content)

    with open("photos_data.json", "w", encoding="utf-8") as f:
        json.dump(web_photos_metadata, f, ensure_ascii=False, indent=2)

    print("✅ photos_data.js 및 photos_data.json 이 성공적으로 업데이트되었습니다!")
    print(f"🎉 모든 작업 완료: 총 {total_selected}장의 사진이 웹 사이트에 반영될 준비를 마쳤습니다.")

def main():
    parser = argparse.ArgumentParser(description="TripDoolee 스마트 사진 자동 정리기")
    parser.add_argument("--raw", default="raw_photos", help="원본 사진 폴더 경로 (기본: raw_photos)")
    parser.add_argument("--output", default="images/photos", help="웹 출력 폴더 경로 (기본: images/photos)")
    parser.add_argument("--max", type=int, default=25, help="Day당 최대 추천 사진 수 (기본: 25)")
    parser.add_argument("--all", action="store_true", help="중복 제거 없이 모든 사진 보존")
    parser.add_argument("--timezone", default="auto", choices=["auto", "pdt", "kst"], help="카메라 시간대 (auto: 자동 감지, pdt: 시애틀 현지, kst: 한국 시간)")

    args = parser.parse_args()
    process_photos(
        raw_dir=args.raw,
        output_dir=args.output,
        max_per_day=args.max,
        all_photos=args.all,
        timezone_mode=args.timezone
    )

if __name__ == "__main__":
    main()
