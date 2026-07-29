from datetime import UTC, datetime, timedelta, timezone

VN_TZ = timezone(timedelta(hours=7), "ICT")

WEEKDAYS_VI = [
    "Thứ Hai",
    "Thứ Ba",
    "Thứ Tư",
    "Thứ Năm",
    "Thứ Sáu",
    "Thứ Bảy",
    "Chủ Nhật",
]

QUARTERS = {1: "Q1", 2: "Q1", 3: "Q1", 4: "Q2", 5: "Q2", 6: "Q2", 7: "Q3", 8: "Q3", 9: "Q3"}


def now_vn() -> datetime:
    return datetime.now(UTC).astimezone(VN_TZ)


def quarter_of(month: int) -> str:
    return QUARTERS.get(month, "Q4")


def today_context() -> str:
    n = now_vn()
    weekday = WEEKDAYS_VI[n.weekday()]
    last_year = n.year - 1
    return (
        f"HÔM NAY: {weekday}, ngày {n.day:02d}/{n.month:02d}/{n.year} "
        f"({quarter_of(n.month)}/{n.year}, giờ Việt Nam UTC+7).\n"
        f"Quy tắc về độ mới của thông tin:\n"
        f"- Ưu tiên nguồn từ {last_year}–{n.year}. Số liệu tài chính, quy mô, nhân sự, "
        f"giá và tin tức phải là bản mới nhất tìm được.\n"
        f"- Nếu chỉ có số liệu cũ hơn {last_year}, VẪN dùng được nhưng phải ghi rõ năm "
        f"của số liệu trong `summary` (ví dụ 'theo báo cáo 2023') và hạ `confidence`.\n"
        f"- Không trình bày dữ liệu cũ như thể là hiện tại. Không suy diễn số liệu "
        f"{n.year} từ số liệu năm cũ.\n"
        f"- Khi hai nguồn xung đột, lấy nguồn mới hơn và nêu rõ mốc thời gian."
    )


def freshness_hint() -> dict[str, str]:
    n = now_vn()
    return {"year": str(n.year), "lastYear": str(n.year - 1), "iso": n.date().isoformat()}
