# ZB Business Development Agent — Kiến trúc & Tiến độ

Tài liệu duy nhất mô tả **đang làm tới đâu** và **logic của từng thành phần**.
Code không chứa comment; mọi giải thích nằm ở đây.

- Hợp đồng API: `API-Backend.md` — nằm trong project Claude Design
  (`ZB Webapp Prototype`), là đặc tả gốc §1–§13 mà backend hiện thực hoá theo.
  Chưa copy về repo; nếu muốn có bản local thì tải từ project Design về gốc repo.
- Prototype UI: `src/frontend/ZB Webapp Prototype (Standalone).html` — bản chạy
  được, có đủ logic React. (Bản `.dc.html` lấy qua API bị cắt ở 256 KiB, xem §9.1.)
- Backend: `src/backend` (FastAPI, quản lý phụ thuộc bằng `uv`)

---

## 1. Tiến độ

| # | Phần việc | Trạng thái |
|---|---|---|
| 1 | Scaffold backend bằng `uv` + docker compose (postgres/redis/minio) | Xong |
| 2 | Mô hình hoá database đầy đủ theo `API-Backend.md` (40 bảng) | Xong |
| 3 | Tích hợp Resend, LangSearch/Tavily, VNG MaaS | Xong |
| 4 | Toàn bộ endpoint §1–§13 (116 path / 150 operation) | Xong |
| 5 | Job nền + SSE: Deep Research, Generate Proposal, Export | Xong |
| 6 | Seed data, migration, chạy thật end-to-end | Xong |
| 7 | Prototype HTML chạy được + import ảnh | Xong (bản Standalone) — ảnh 6/25 qua API, xem §9 |
| 8 | Nối prototype vào dữ liệu thật từ DB | Xong — xem §9b |
| 9 | Quản lý user/admin trong DB | Xong — xem §9c |

Quy mô hiện tại: 71 file Python, ~15.900 dòng, 40 bảng, 150 operation.

### Đã kiểm chứng bằng cách chạy thật

Không phải kiểm tra bằng mắt — các mục dưới đây đã gọi API thật trên stack thật:

- 48/48 endpoint GET trả 200, không endpoint nào lỗi.
- Luồng OTP đầy đủ, đúng cả 3 mã lỗi: `AUTH_DOMAIN`, `AUTH_UNKNOWN`, `OTP_INVALID`
  (kèm `attemptsLeft`).
- Deep Research trên **Napas**: enqueue → worker nhận → SSE phát `step`
  (“Đang tìm nguồn công khai về Napas tại Việt Nam”) → dừng đúng dạng
  `RESEARCH_503` có `sources[]` + `reasons[]` như màn lỗi UI cần.
- Export PDF thật: 5 trang, **dấu tiếng Việt hiển thị đúng** (0 ký tự hộp).
- Tải file KB: kết quả theo từng file, `FILE_TYPE`, `FILE_SIZE`, và dedupe theo
  hash nội dung (lần 2 trả lại đúng `fileId` cũ kèm `deduplicated: true`).
- Trích xuất tài liệu: DOCX ra đúng tiếng Việt, PDF thật ra 5 trang.
- Versioning: `v1.0 → v1.1 → v1.2`, diff hoạt động, restore sinh bản mới.
- Đếm ở `/dashboard` khớp đúng với `/customers` và `/proposals` khi áp cùng bộ lọc.
- `ruff check` sạch, `alembic check` không phát hiện lệch schema.
- **Prototype chạy thật trong browser**: đăng nhập OTP thật, dashboard/Khách hàng/
  Proposal/KB/Skills/Template đều hiển thị dữ liệu DB; Product Knowledge giữ
  catalogue tĩnh; 0 ảnh vỡ.

---

## 2. Hình dạng hệ thống

```
                    ┌──────────────────────────────┐
   Browser ───────▶ │ FastAPI  (uv run uvicorn)     │
   (prototype)      │  /api/v1 + serve src/frontend │
                    └───┬──────────┬─────────┬──────┘
                        │          │         │
                 ┌──────▼───┐ ┌────▼────┐ ┌──▼──────┐
                 │ Postgres │ │  Redis  │ │  MinIO  │   ← chỉ 3 image này
                 │   :5432  │ │  :6379  │ │  :9000  │     chạy trong Docker
                 └──────▲───┘ └────▲────┘ └──▲──────┘
                        │          │         │
                    ┌───┴──────────┴─────────┴──────┐
                    │ arq worker (uv run arq)        │
                    │  research · generate · export  │
                    └───┬───────────┬────────────┬───┘
                        │           │            │
                   VNG MaaS    LangSearch/    Resend
              glm-5.2 · gemma   Tavily        (email)
```

Backend và worker chạy trên host qua `uv`; Docker chỉ lo ba service có state.
Postgres extension do migration tạo, MinIO bucket do backend tạo lúc khởi động —
nên không có file cấu hình nào phải mount.

---

## 3. Logic từng thành phần

### 3.1 `app/core` — nền tảng

| File | Logic |
|---|---|
| `config.py` | Toàn bộ cấu hình từ env. List settings viết dạng CSV (`a,b,c`) nên phải đánh dấu `NoDecode`, không thì pydantic-settings cố `json.loads` và vỡ trước khi validator chạy. |
| `errors.py` | Mỗi mã lỗi UI đang xử lý là một class. UI phân nhánh theo `error.code`, **không** theo HTTP status, nên giá trị chuỗi là phần của hợp đồng. `ResearchError` bắt buộc mang `sources`+`reasons`; `ExportError` bắt buộc mang `stage`+`slideIndex`. |
| `text.py` | Bỏ dấu tiếng Việt. `đ/Đ` không phân rã được qua NFD nên map tay. `next_version` bump `v1.4 → v1.5`, cuộn major khi minor tới `.9`. |
| `synonyms.py` | Đồng nghĩa Việt ↔ Anh. Xem §4. |
| `pagination.py` | Phong bì `{items,page,size,total,totalPages}`, mặc định 5 dòng đúng như bảng trong UI. |
| `security.py` | JWT access/refresh. Không có password — OTP chỉ lưu HMAC, salt bằng email nên mã không dùng chéo được. |

### 3.2 `app/db` — dữ liệu

40 bảng, chia theo domain: `user`, `customer`, `product`, `proposal`,
`knowledge`, `library` (skill + template), `collab` (share/notification/audit),
`job`, `admin`.

Ba quyết định đáng nêu:

**`EnumStr` (`db/base.py`).** Cột enum khai báo `Mapped[Role]` nhưng lưu
`VARCHAR`. Nếu chỉ dùng `String(16)` thì khi đọc lại từ DB ta nhận về `str`
thuần, và `.value` nổ — đúng lỗi đã gặp ở export. `EnumStr` là `TypeDecorator`
ép về enum lúc load, DDL không đổi (`alembic check` xác nhận không lệch).
Đã áp cho 35 cột.

**Khoá chính dạng chuỗi có tiền tố.** `cus_…`, `prp_…`, `job_…` — giống hệt
những gì prototype hiển thị, không lộ UUID trần cũng không dùng số tăng dần
đoán được.

**Cột `*_norm`.** Bản không dấu của `name`/`website`/`title` được ghi song song
để tìm kiếm không phải gọi hàm trên từng dòng. `search_norm` trên `audit_logs`
cũng vậy.

### 3.3 `app/integrations` — dịch vụ ngoài

**`maas.py` — VNG Cloud MaaS.** Surface tương thích OpenAI. Hai model:
`z-ai/glm-5.2` cho suy luận (research, viết proposal, khớp sản phẩm, chat editor)
và `google/gemma-4-31b-it` cho thị giác (PDF scan, ảnh slide, logo).
Turn hệ thống dùng role `assistant` — đúng như ví dụ curl của MaaS cho GLM.
`Completion.as_json()` cố ý dễ tính: bóc ```` ```json ````, và nếu vẫn lỗi thì
lấy đoạn `{…}` ngoài cùng, vì model vẫn lệch format dù đã ra chỉ thị.
Nếu `content` rỗng thì lấy `reasoning_content` để không trả về chuỗi trắng.

**`search.py` — LangSearch → Tavily.** Failover trái sang phải. Phân biệt rõ
hai kiểu thất bại vì UI có hai màn khác nhau: gọi được nhưng không có kết quả
→ `RESEARCH_424`; không provider nào phản hồi → `RESEARCH_503`.
Xem §4 về phần giới hạn thị trường Việt Nam.

**`resend_client.py` — email.** Template HTML theo màu thương hiệu
(`#0068FF`, `#021F4E`), layout dạng table cho mail client. Phủ OTP, mời thành
viên, chia sẻ, đề nghị/kết quả duyệt, research xong, bình luận, xin quyền,
bàn giao. `EMAIL_DRY_RUN=true` in ra log kèm mã OTP — dev không tốn quota và
không gửi ra hộp thư thật.

### 3.4 `app/services` — nghiệp vụ dùng chung

**`shares.py` — một bộ phân quyền cho 5 loại tài nguyên.** §8b nói rõ hộp thoại
chia sẻ là **cùng một component** cho Customer/Proposal/KnowledgeBase/Skill/
Template, nên đây là một bảng đa hình, không phải 5 bộ endpoint song song.
Thứ tự quyết định quyền (khớp đầu tiên thắng): owner → ADMIN → share cho user →
share cho team → link toàn công ty (nếu policy còn cho phép) → scope `Shared` →
không có quyền (UI vẽ thẻ nét đứt kèm nút xin quyền).
`apply_visibility()` đưa cùng logic đó vào mệnh đề SQL, nên danh sách không bao
giờ lộ dòng mà mở ra sẽ bị chặn.

**`events.py` — stream tiến độ.** Hai nửa: Redis pub/sub để fan-out trực tiếp,
và bảng `job_events` làm log bền. Redis chỉ tới được client **đang** kết nối, nên
tab mở lại giữa lúc chạy sẽ replay từ `seq` và dựng lại log, danh sách nguồn,
thanh tiến độ đúng như cũ. Ghi DB trước rồi mới publish — client kết nối lại
giữa hai bước vẫn tìm thấy event.

**`audit.py`.** Ghi tự động ở mọi endpoint có thay đổi (§3, §5, §6, §7, §8, §12).
Không commit — bản ghi audit và thay đổi nó mô tả phải cùng vào hoặc cùng không.
Danh mục suy ra từ tiền tố action (`customer.*` → “Dữ liệu KH”).

**`storage.py` — MinIO.** SDK `minio` là đồng bộ, nên mọi lệnh đẩy sang thread;
block event loop khi upload vài MB sẽ làm nghẽn mọi request khác.
Bốn bucket: knowledge / exports / templates / avatars.
Link tải sinh **lúc bấm**, không sinh sẵn lúc render — URL presign tạo từ trước
thường đã hết hạn khi người dùng mới bấm Download.

**`queue.py`.** Enqueue thất bại **không** rollback transaction của caller: dòng
job đã nằm trong DB ở trạng thái `queued` nên có thể dispatch lại, thay vì mất
việc người dùng vừa tạo.

### 3.5 `app/api/v1` — endpoint

Một router cho mỗi mục của đặc tả. Vài chỗ có logic đáng nêu:

- **`dashboard.py`** — mỗi ô KPI bấm được và điều hướng kèm bộ lọc, nên con số ở
  đây và kết quả list khi áp cùng bộ lọc phải khớp; cả hai đi qua cùng
  `apply_visibility` và cùng điều kiện owner.
- **`proposals.py`** — `reorder` ghi hai lượt qua offset âm, vì unique
  `(proposal_id, index)` sẽ vỡ giữa lúc cập nhật nếu ghi thẳng giá trị cuối.
  `approve` chỉ chuyển trạng thái khi **mọi** người duyệt trong vòng đó đã đồng ý;
  một lượt từ chối là quyết định cuối. `reject` bắt buộc có lý do.
- **`ai.py`** — stream token, nhưng khối ```` ```json ```` không được lọt ra bong
  bóng chat; nó được bóc thành `suggestedChanges[]` để UI hiện nút “Áp dụng”.
  Model **không bao giờ** tự sửa deck — apply là một call riêng, tường minh.
  Generator stream tự mở session riêng, vì session theo request đã đóng ngay khi
  handler trả response.
- **`knowledge.py`** — upload trả kết quả **theo từng file**, không all-or-nothing,
  và dedupe theo SHA-256 nên retry một lô lỗi dở không ghi trùng.
- **`shares.py`** — `/shares/link` phải khai **trước** `/shares/{share_id}`, nếu
  không `"link"` bị bắt làm path param (đã gặp và đã sửa).
- **`admin.py`** — không cho hạ vai trò Admin cuối cùng; không cho xoá người còn
  đang phụ trách khách hàng (phải bàn giao trước); khoá tài khoản là thu hồi luôn
  refresh token, nếu không người bị khoá vẫn làm việc tới lúc token hết hạn.

### 3.6 `app/workers` — job nền

`base.py` cho mọi task cùng một khung: `claim` → phát `step` → `check_cancel`
giữa các bước → `finish`/`fail`. Huỷ là **hợp tác**: nút Dừng bật cờ trong Redis,
worker đọc giữa hai bước rồi thoát sạch — không cắt giữa lúc ghi.

**`research.py` — Deep Research.** Xem §4 (phần quan trọng nhất).

**`generate.py` — Generate Proposal.** Hai pha: dựng dàn ý, rồi viết từng slide,
phát một event `slide` mỗi slide xong để UI điền dần chứ không đợi cả lượt.
Deck cũ chỉ bị xoá **sau khi** dàn ý mới đã hợp lệ — dàn ý lỗi thì slide cũ còn
nguyên. Một slide lỗi không đánh sập cả bộ 12 slide: đánh dấu `failed` và đi tiếp
để người dùng còn thứ sửa được.

**`export.py` — PDF.** Bốn bước như UI liệt kê: prepare → render → embed →
package. `stage` được lưu nên lỗi chỉ đúng bước dừng và đúng slide gây lỗi, và
retry **tiếp tục** từ đó: nếu file đã render và còn trong MinIO thì dùng lại,
không render lại. `_SlideRenderError` mang theo index để UI gọi tên slide.

**`fonts.py`.** Font mặc định của reportlab không có glyph tiếng Việt — PDF sẽ
ra ô vuông. Đây là lỗi thật đã gặp. Nay dò một danh sách font Unicode
(DejaVu/Liberation/Noto trên Linux, Arial Unicode trên macOS), thử render thử
chuỗi có dấu trước khi nhận, và cảnh báo rõ nếu phải rơi về Helvetica.
Dockerfile cài `fonts-dejavu-core` để container Linux luôn có font.

**`knowledge.py` — index tài liệu.** PDF/DOCX/XLSX/PPTX bóc text trực tiếp
(DOCX/XLSX đọc thẳng OOXML, không cần thêm dependency). PDF không có lớp text
(bản scan) thì đưa qua model thị giác `gemma-4-31b-it`. Một trang lỗi không làm
dừng cả file.

---

## 4. Deep Research chỉ phục vụ thị trường Việt Nam

Đây là ràng buộc sản phẩm, không phải tuỳ chọn. Với **Napas**, kết quả phải là
Napas **tại Việt Nam** — sản phẩm trong nước, ngân hàng đối tác trong nước, bối
cảnh quy định của NHNN — chứ không phải công ty trùng tên ở nước khác, cũng
không phải hoạt động của công ty mẹ ở thị trường ngoài Việt Nam.

Ràng buộc đó được ép ở **ba tầng**, vì chỉ một tầng thì rò:

**1. Tầng truy vấn** (`research.build_queries`) — truy vấn tiếng Việt, luôn neo
vào Việt Nam bằng cụm `Việt Nam` hoặc bằng `site:` trỏ domain trong nước:

```
site:napas.com.vn giới thiệu
Napas Việt Nam sản phẩm dịch vụ khách hàng
"Napas" Việt Nam tin tức mới nhất
Napas (site:cafef.vn OR site:vnexpress.net OR site:vietnamnet.vn OR …)
```

**2. Tầng xếp hạng** (`search.vn_rank`) — provider trả gì thì trả, ta xếp lại:
domain báo kinh tế trong nước `+3.0`, `.vn` `+2.5`, `.com.vn`/`.gov.vn` thêm
`+0.5`, có dấu tiếng Việt trong tiêu đề/trích đoạn `+1.5`, có cụm “Việt Nam”
`+1.0`. Các aggregator toàn cầu (Crunchbase, ZoomInfo, Glassdoor…) bị loại thẳng
vì chúng trả nội dung thị trường khác cho truy vấn Việt Nam.
Provider cũng được truyền `country=VN` / `search_lang=vi` khi hỗ trợ.

**3. Tầng rút dữ kiện** (prompt `EXTRACT_SYSTEM`) — model được chỉ thị tường minh:
chỉ lấy dữ kiện về thị trường Việt Nam; nguồn nói về thị trường khác thì **bỏ
nguồn đó**; nếu là pháp nhân Việt Nam của tập đoàn nước ngoài thì chỉ giữ phần
liên quan Việt Nam. Hồ sơ lưu kèm `profile.market = "Việt Nam"` để phía sau
không mơ hồ.

Cấu hình: `RESEARCH_MARKET`, `RESEARCH_LOCALE`, `RESEARCH_PREFERRED_DOMAINS`,
`RESEARCH_EXCLUDED_DOMAINS`.

**Khi không đủ nguồn** — hồ sơ **vẫn được tạo** với `factCount = 0`, đúng như đặc
tả, để BD nhập tay hoặc tải tài liệu lên thay thế; kèm bảng từng nguồn
(Đã lấy / Bị chặn / Chưa chạy) và danh sách lý do dừng.

### Agentic loop — model tự lái việc tìm kiếm

Trước đây Deep Research chạy một danh sách truy vấn cố định rồi rút dữ kiện. Nay
`app/workers/research_agent.py` để **model điều phối việc tìm kiếm**:

1. **Seed** — 8 truy vấn tất định, neo VN (`site:` domain chính thức, báo kinh tế
   trong nước, trang tuyển dụng). Đây là sàn tối thiểu, không phụ thuộc model.
2. **Vòng planner** — model nhận: ngày hôm nay, các truy vấn đã chạy, **góc còn
   thiếu**, và bản rút gọn toàn bộ nguồn đã thu. Nó trả JSON gồm `assessment`,
   `covered`, `searches[]` (mỗi truy vấn kèm `angle` + cờ `recent`), và `done`.
3. **Thực thi** — chạy các truy vấn đó, dedupe theo URL, cờ `recent` sẽ bật
   `freshness=oneYear` (LangSearch) / `topic=news&days=365` (Tavily).
4. **Lặp** cho tới khi model báo `done`, hết góc thiếu, hoặc đụng trần nguồn.
5. **Extract** — nạp toàn bộ corpus (theo `input_budget_chars` của profile) để
   rút dữ kiện.

10 góc mặc định: `profile`, `products`, `scale`, `market`, `customers`,
`digital`, `hiring`, `news`, `regulation`, `painpoints`.

Không dùng native tool-calling: giao thức JSON hoạt động trên mọi model
OpenAI-compatible, và khi model trả JSON lỗi thì vòng lặp chỉ dừng lại chứ không
làm sập job — luôn còn dữ liệu từ seed.

**Đã chạy thật trên Napas** (glm-5.2 + LangSearch live):

| | Trước khi sửa | Sau |
|---|---|---|
| Vòng planner | 0 | 1 |
| Truy vấn | 8 | 14 |
| Nguồn | 45 | 79 |
| Dữ kiện | 22 | 27 |
| Góc phủ | 6/10 (thiếu 4) | 13 (thiếu 0) |

Đánh giá do model tự viết ở vòng 1: *"Đã có thông tin tổng quan, sản phẩm, doanh
thu và đối thủ. Cần tìm hiểu sâu hơn về tệp khách hàng cụ thể, hiện trạng chuyển
đổi số/kênh CSKH, quy định pháp lý ảnh hưởng đến dữ liệu/liên lạc, và các điểm
đau về chi phí/tự động hóa."* Model còn **tự thêm góc** ngoài danh sách
(`competitors`, `financials`, `overview`).

**Một bug đã gặp và sửa:** `research_max_sources` để 14, mà seed đã ra 45 nguồn
nên vòng lặp `break` ngay trước vòng 1 — planner không bao giờ chạy (`rounds=0`).
Nay trần là 80, và điều kiện dừng đổi thành "hết góc thiếu **hoặc** đụng trần
cứng (1,5× trần)", nên số nguồn nhiều không còn giết vòng lặp.

### Context về thời gian hiện tại

`app/core/clock.py::today_context()` được chèn vào prompt của planner, extract,
matching và proposal outline:

```
HÔM NAY: Thứ Tư, ngày 29/07/2026 (Q3/2026, giờ Việt Nam UTC+7).
- Ưu tiên nguồn từ 2025–2026...
- Nếu chỉ có số liệu cũ hơn 2025, VẪN dùng được nhưng phải ghi rõ năm ... và hạ confidence.
- Không trình bày dữ liệu cũ như thể là hiện tại.
- Khi hai nguồn xung đột, lấy nguồn mới hơn và nêu rõ mốc thời gian.
```

Hiệu quả thấy rõ trong dữ kiện thật của Napas: *"Tính đến tháng 7/2026, Napas đã
kết nối thanh toán QR xuyên biên giới với 6 quốc gia"*, *"Tháng 7/2026, Napas và
TikTok Shop ký hợp tác"*, *"Tính đến tháng 10/2025, gần 90 triệu tài khoản mobile
banking sử dụng VietQR"* — model tự nêu mốc thời gian thay vì nói chung chung.

### Tìm kiếm tiếng Việt: bỏ dấu là chưa đủ

Đặc tả nêu thẳng: gõ `"ca phe"` phải ra **“The Coffee House”**. Bỏ dấu không giải
quyết được — tên đó không chứa chuỗi “ca phe” ở bất kỳ dạng nào. Cần thêm lớp
đồng nghĩa Việt ↔ Anh (`core/synonyms.py`), mở rộng truy vấn rồi OR trên các
biến thể:

```
"ca phe"  → ca phe | cafe | coffee      → The Coffee House, Highlands Coffee
"Cà phê"  → (bỏ dấu) như trên           → giống hệt
"ngan hang" → ngan hang | bank | banking → TPBank
"hang khong" → hang khong | air | airline | aviation → Vietjet Air
"bach hoa" → bach hoa | grocery | mart   → Bách Hoá Xanh
```

Áp cho danh sách khách hàng, danh sách proposal và ⌘K toàn cục.

---

## 5. Bốn quy tắc xuyên suốt trong đặc tả

**Version tự tăng.** Skill, Template và Proposal cùng một luật. Lịch sử chứa
**mọi** version kể cả version hiện tại — nếu chỉ chứa version cũ thì không diff
được với bản đang dùng, và đó chính là lỗi đã gặp. Nên trình tự là:
áp thay đổi → bump → chụp trạng thái **mới** vào lịch sử. Restore cũng sinh bản
mới, không ghi đè.

**Một bộ API chia sẻ cho 5 loại tài nguyên.** §3.4 ở trên.

**Bỏ dấu tiếng Việt ở mọi tham số `q`.** Không chỉ tìm kiếm toàn cục. §4 ở trên.

**Audit ghi tự động.** Không có endpoint tạo; UI chỉ đọc.

---

## 6. Bảng mã lỗi

UI phân nhánh theo `error.code`. Phong bì:
`{"error": {"code", "message", "field", …}}`

| Code | Nơi hiện | Trả kèm |
|---|---|---|
| `AUTH_DOMAIN` | Đăng nhập | `field: email` |
| `AUTH_UNKNOWN` | Đăng nhập | `field: email` |
| `AUTH_503` | Đăng nhập | — |
| `OTP_INVALID` | Nhập OTP | `attemptsLeft` |
| `OTP_EXPIRED` | Nhập OTP | — |
| `OTP_LOCKED` | Nhập OTP | `lockedUntil` |
| `SERVICE_503` | Banner toàn app | `degraded[]` |
| `FILE_TYPE` / `FILE_SIZE` | Upload | `allowed[]` / `maxMb` |
| `STORAGE_500` / `STORAGE_503` | Upload KB | kết quả từng file |
| `EXPORT_422` / `EXPORT_503` | Kết xuất | **`stage`, `slideIndex`** |
| `RESEARCH_424` / `RESEARCH_503` | Deep Research | **`sources[]`, `reasons[]`** |

Hai dòng in đậm là ràng buộc bắt buộc: màn lỗi của UI cần chính những field đó
mới vẽ được bảng từng nguồn / chỉ đúng bước dừng.

---

## 7. Chạy

```bash
cp .env.example .env          # điền RESEND_API_KEY, AI_PLATFORM_API_KEY, …
docker compose up -d          # postgres + redis + minio

cd src/backend
uv sync --extra dev
uv run alembic upgrade head   # migration cũng tạo unaccent + pg_trgm
uv run python -m app.seed     # dữ liệu demo
uv run uvicorn app.main:app --reload
```

Shell thứ hai cho worker (Deep Research / Generate / Export):

```bash
cd src/backend && uv run arq app.workers.settings.WorkerSettings
```

| | |
|---|---|
| API | http://localhost:8000/api/v1 |
| Swagger | http://localhost:8000/docs |
| Prototype | http://localhost:8000/ (same-origin, không cần CORS) |
| MinIO console | http://localhost:9001 |

Tài khoản demo (OTP in ra log worker/API khi `EMAIL_DRY_RUN=true`):

| Email | Vai trò |
|---|---|
| `khanhpp@vng.com.vn` | ADMIN |
| `anhtuan@vng.com.vn` · `minhthu@vng.com.vn` · `quochuy@vng.com.vn` · `thanhha@vng.com.vn` | MEMBER |

Kiểm tra chất lượng:

```bash
uv run ruff check app && uv run ruff format app
uv run mypy app
uv run alembic check
```

---

## 8. Biến môi trường quan trọng

| Biến | Ý nghĩa |
|---|---|
| `AI_PLATFORM_API_KEY` | MaaS. Thiếu → research/generate trả lỗi rõ ràng, không lỗi âm thầm |
| `MAAS_REASONING_MODEL` | `z-ai/glm-5.2` |
| `MAAS_VISION_MODEL` | `google/gemma-4-31b-it` |
| `LANGSEARCH_API_KEY` / `TAVILY_API_KEY` | Nguồn cho Deep Research; failover theo `SEARCH_PROVIDERS` |
| `RESEND_API_KEY` | Email; `EMAIL_DRY_RUN=true` để in ra log |
| `RESEARCH_MARKET` / `RESEARCH_LOCALE` | `VN` / `vi` — phạm vi thị trường |
| `ALLOWED_EMAIL_DOMAINS` | `vng.com.vn` — chặn `AUTH_DOMAIN` |
| `MAX_UPLOAD_MB` | 50, khớp `FILE_SIZE` |

---

## 9. Việc còn dở — và vì sao bị chặn

Phần này **không phải chưa làm**, mà là **bị chặn bởi giới hạn công cụ**. Ghi lại
chính xác đã thử gì, kết quả ra sao, và cần gì để mở.

### 9.1 Đuôi prototype HTML — chặn cứng

`src/frontend/ZB Webapp Prototype.dc.html` hiện **đúng 262.144 byte = đúng 256
KiB**, tức đụng trần đọc file của API Claude Design (`truncated: true`).
Markup còn, nhưng **toàn bộ khối `<script data-dc-script>` chứa logic React bị
cắt mất**, nên trang không chạy được. `support.js` (69.150 byte) nguyên vẹn.

Đã thử:
- Đọc lại nhiều lần → luôn cắt đúng 262.144 byte, không có tham số offset/range.
- **Chạy `/design-login`** (đã cấp quyền: "Design-system access authorized") →
  đọc lại **vẫn đúng 262.144 byte, `truncated: true`**. Trần 256 KiB nằm trong
  bản thân công cụ `DesignSync.get_file`, không phải vấn đề xác thực. Sau khi
  login cũng **không xuất hiện tool MCP design nào mới**.
- Mở project Design bằng browser (Playwright) để tải thẳng file → **bị chuyển về
  `/login`**, browser đó có profile riêng, không dùng session claude.ai của máy.

### 9.2 Ảnh nhị phân — 3/25 file

**Đã lấy được 6/25, tất cả đã kiểm CRC từng chunk:**

| File | Kích thước |
|---|---|
| `pptx/media/image102.png` | 1.224 B · 82×74 |
| `pptx/media/image103.png` | 1.579 B · 60×75 |
| `pptx/media/image104.png` | 1.023 B · 59×70 |
| `pptx/media/image105.png` | 1.556 B · 77×72 |
| `pptx/media/image108.png` | 1.373 B · 62×62 |
| `pptx/media/image17.png` | 3.101 B · 161×162 |

**Còn thiếu 19/25:** `image107`, `image133`, `image135`, `image136`, `image137`,
`image149`, `image33`, `image34`, `image35`, `image38`, `image46`, `image71`,
`image72`, `image74`, `image90`, `image93`, và 3 file `assets/`.

Ảnh trả về dạng base64 **inline** (không được ghi ra file như file lớn), nên
đường duy nhất là chép tay chuỗi base64 — và cách đó **không đáng tin**:

- `logo-color.png` (base64 ~18 KB): chép tay ra file hỏng — `IDAT truncated`.
- `image104.png` (base64 ~1 KB): chép tay ra `IDAT CRC mismatch`.

Vì vậy mọi lần ghi đều đi qua **cổng kiểm tra CRC từng chunk PNG + bắt buộc có
IEND**; file nào không sạch thì **không ghi**. Nhờ đó 6 file trên đúng bit, và
**không có file hỏng nào lọt vào repo**.

Nhưng đây là xác suất, không phải kỹ thuật: tỷ lệ hỏng ~1/3 với file nhỏ và tăng
theo độ dài chuỗi. `image107` chép **thất bại 2 lần liên tiếp**; `image33`
(~5 KB base64) ra chuỗi sai độ dài, decode không nổi. Cứ retry thì có lúc được
(`image104` đúng ở lần 2, `image17` đúng ngay lần đầu dù 3 KB), nhưng đó là
xổ số — không phải cách nên dùng cho 19 file còn lại, và **kể cả lấy đủ 25 ảnh
thì trang vẫn không chạy** vì thiếu đuôi HTML ở §9.1.

### 9.2b Cách đã hoạt động: giải nén từ PPTX

Đây là phần quan trọng — **có một đường lấy đúng bit, không chép tay**:

Khi kết quả tool đủ lớn (>~25 KB), runtime **tự ghi cả kết quả ra file trên đĩa**
thay vì trả inline. Lúc đó decode từ file đó → **không có bước chép tay → đúng
100% bit**.

Lợi dụng điều này: `pptx/media/` chính là media giải nén từ
`uploads/Hackathon 2026_contestant_PPXT Template.pptx`. File pptx đủ lớn để được
ghi ra đĩa, nên:

1. `get_file` file pptx → runtime ghi ra đĩa (bị cắt còn 196.608 byte, nhưng ZIP
   đặt *local file header* tuần tự từ đầu nên phần đầu vẫn đọc được).
2. Quét thủ công các `PK\x03\x04`, đọc `method` / `csize` / `crc32`.
3. Media trong pptx lưu **method=0 (STORED, không nén)** → copy thẳng byte.
4. Đối chiếu **CRC32 mà chính ZIP header lưu sẵn** → bằng chứng toàn vẹn tuyệt đối.

Kết quả: **41 ảnh lấy đúng bit trong một lần**, tất cả khớp CRC32.
Bằng chứng chéo: `image17.png` giải nén ra 3.101 byte — **trùng khít** với bản
tôi chép tay trước đó, xác nhận `ppt/media/` và `pptx/media/` là cùng một bộ file.

Hiện `src/frontend/pptx/media/` có **46 PNG, 0 file lỗi**.

Phần còn thiếu (`image71` trở lên) nằm **sau điểm cắt** của pptx nên không lấy
được bằng cách này; ảnh trong pptx chỉ tới `image69`.

### 9.3 Cần gì để mở

Còn thiếu **14/25**: `image107`, `image133`, `image135`, `image136`, `image137`,
`image149`, `image71`, `image72`, `image74`, `image90`, `image93`, và 3 file
`assets/`.

Chọn một trong ba, **cách 1 là tốt nhất** vì dùng lại đúng kỹ thuật đã chứng
minh hoạt động ở §9.2b:

1. **Nén 14 file thiếu thành 1 file `.zip` rồi upload vào project Design.**
   14 file ≈ 65 KB → zip ≈ 60 KB, vừa **trên** ngưỡng ~25 KB (nên được ghi ra
   đĩa, đúng bit) và vừa **dưới** trần ~192 KB (nên không bị cắt). Tôi
   `get_file` cái zip đó rồi giải nén + đối chiếu CRC32 → xong toàn bộ trong
   một lượt, không chép tay dòng nào.
   *Cách này cũng áp được cho `.dc.html` nếu nén lại: HTML ~330 KB nhưng nén zip
   còn khoảng 60–80 KB → lọt trần → **lấy được cả đuôi script còn thiếu**.*
2. **Tự lưu file từ app Design** vào `src/frontend/`. Chắc chắn, không cần tôi
   dò gì.
3. **Đăng nhập claude.ai trong cửa sổ Playwright** đang mở. Sau đó tôi tải được
   đúng bit qua `browser_evaluate` (ghi trực tiếp ra file, không đi qua context).

Đã loại: `/design-login` (§9.1) và `uploads/zbs-brand-toolkit.pdf` (PDF nặng
~6 MB, `/L 6076430`, bị cắt quá sớm nên không chứa tới logo).

### 9.4 Hai điểm cần biết thêm

- HTML hiện có tham chiếu 4 file **không** nằm trong danh sách ban đầu:
  `pptx/media/bg1.jpg`, `image36.png`, `image47b.png`, `image65.png`. Danh sách
  asset đầy đủ chỉ chốt được khi có đuôi file.
- **Backend không bị chặn bởi việc này.** `API-Backend.md` là hợp đồng đầy đủ,
  đã hiện thực hoá và chạy thật xong theo đó.

---

## 9b. Nối prototype vào dữ liệu thật

Prototype gốc chứa mock data cứng trong các method có cache (`_CUST`, `_KBS`,
`_SKILLS`, `_TPLS`, `_PROPOSALS`, `_DIR`). Cách nối:

**Không sửa file gốc của bạn.** `src/backend/app/services/prototype.py` patch HTML
**tại thời điểm serve** rồi cache theo mtime. Bạn export lại prototype thì chỉ cần
reload — patch tự áp lại.

Mỗi seam được chèn một guard rất nhỏ ở đầu method:

```js
_CUST() {
  const __zb = window.__ZB;
  if (__zb && __zb.ready && __zb.customers) return __zb.customers;
  if (this.__cust) return this.__cust;          // bridge chưa nạp -> giữ mock
  ...
}
```

Điều kiện là **`ready`**, không phải `length`. Khác biệt này quan trọng: nếu xét
`length` thì **danh sách rỗng hợp lệ sẽ bị coi là "chưa có dữ liệu" và mock hiện
lại** — xoá sạch Skill/Template trong DB là thấy ngay dữ liệu giả quay về. Với
`ready`, một lần bridge nạp xong thì DB là nguồn duy nhất, kể cả khi rỗng.

Fallback về mock chỉ còn đúng một trường hợp: **bridge chưa nạp được** (API chết,
chưa đăng nhập). Nhờ đó API chết thì UI vẫn chạy, không trắng màn.

### Dashboard lệch với màn Khách hàng — nguyên nhân và cách sửa

**Nguyên nhân (bug, đã sửa).** Bridge gọi `loadAll()` kiểu fire-and-forget khi
phát hiện token trong `localStorage`. React mount và render **trước khi** fetch
xong → Dashboard dựng bằng mock. Dữ liệu thật về sau đó nhưng **không có gì bảo
React render lại**, nên Dashboard đứng nguyên với số liệu mock; bấm sang màn Khách
hàng thì `setState` kích hoạt render mới → ra dữ liệu thật. Kết quả: hai màn nói
hai chuyện.

Sửa bằng một kênh thông báo nhỏ: bridge có `ZB.subscribe(fn)` và gọi `emit()` sau
khi `loadAll()` xong (cả nhánh lỗi) và sau `logout()`; `patch_prototype()` chèn vào
`componentDidMount` một dòng đăng ký `forceUpdate`. Đã kiểm chứng: reload có token
→ Dashboard hiện `VIE`, `NAPAS`, `TCH` là dữ liệu DB, `url("undefined")` = 0.

**Nguyên nhân 2 (bug thật, đã sửa): "Xem tất cả" dẫn sang bảng trống.**

Prototype chỉ có **hai** scope khách hàng — `Của tôi (0)` và `Được chia sẻ (1)` —
và mặc định là `0`:

```js
const sc = this.state.custScope || 0;
if (sc === 0 ? !!c.owner : !c.owner) return false;
```

Trong DB **không khách hàng nào thuộc `khanhpp`** (chủ là `minhthu`, `anhtuan`,
`thanhha`, `quochuy`, `zaloadmin`), nên `Của tôi` lọc ra 0 dòng. Còn Dashboard
không lọc theo chủ sở hữu nên hiện đủ. Bấm "Xem tất cả" (`this.go('customers')`,
không đổi filter) là rơi vào đúng cái mặc định đó → "Chưa có khách hàng nào".

Không có scope nào hiển thị **toàn bộ**: `0` chỉ ra của mình, `1` chỉ ra được chia
sẻ. Nên đã thêm scope thứ ba `['Tất cả', 2]` bỏ qua kiểm tra chủ sở hữu và **đặt
làm mặc định** (`custScope === undefined ? 2 : …`), để màn Khách hàng khớp với
Dashboard. `custClear` (nút "Xóa bộ lọc") cũng reset về `2`.

Đã kiểm chứng: Dashboard → "Xem tất cả" → chip `Tất cả (9) · Của tôi (0) ·
Được chia sẻ (9)`, bảng `1–5 trên 9 khách hàng` với đúng owner từ DB.

> Ghi chú: trong một lần kiểm tra trước tôi kết luận sai rằng màn này "không lọc
> mặc định" — phép đo đó bị nhiễu vì `custScope` còn sót giá trị từ các cú click
> trước trong cùng session. Tái hiện từ trạng thái sạch mới thấy đúng bug.

Nhân đây siết `ownerOf()` trong bridge: trước đó nó so **chuỗi handle**, chỉ đúng
nhờ serializer tình cờ đặt handle vào field `name`. Nay so `id` trước rồi mới tới
handle — nếu serializer đổi sang tên hiển thị thì quyền sở hữu vẫn đúng.

`src/frontend/zb-api-bridge.js` gọi `/api/v1` rồi map sang đúng shape prototype
(`key`, `short`, `init`, `tone`, `updated` dạng "10 phút trước"…). Hai enum khớp
sẵn nên không cần dịch: `CustomerStatus` (`Đang Deep Research`, `Hoàn tất nghiên
cứu`…) và `ProposalStatus` (`Draft` / `Hoàn tất`).

### Cái gì lấy từ DB, cái gì không

| Màn | Nguồn |
|---|---|
| Đăng nhập OTP | **API thật** — `/auth/otp/request` + `/auth/otp/verify` |
| Dashboard, Khách hàng | **DB** |
| Proposal | **DB** |
| Knowledge Base | **DB** |
| Skills | **DB** |
| Template Library | **DB** |
| Danh bạ (chia sẻ, bàn giao) | **DB** — `/directory` |
| Yêu cầu | **DB** — `/access-requests` |
| Người dùng (màn Quản trị) | **DB** — `/admin/users` |
| **Product Knowledge** | **DB** — `/products?detail=true` (trước đây cố ý giữ tĩnh, nay đã nối theo yêu cầu) |
| **Thông báo** (chuông + màn Thông báo) | **DB** — `/notifications?limit=50`, xem §9i |
| **Sản phẩm được đề xuất nhiều** | **DB** — `/dashboard` → `topProducts`, xem §9i |
| **Tạo khách hàng + Deep Research** | **DB + worker thật** — `POST /customers`, `GET /jobs/{id}`, xem §9j |
| **Hồ sơ khách hàng** (`_CRM`) | **DB** — `GET /customers/{id}` → `profile` + `facts` + `sources`, xem §9j |
| **Audit Log** (màn Quản trị) | **DB** — `GET /admin/audit` + `/admin/audit/{id}`, xem §9k |

Patch report in ra log mỗi lần render; seam nào không khớp thì phần đó tự giữ mock:

```
prototype_patched applied=['_CUST','_KBS','_SKILLS','_TPLS','_DIR','_LOCKED',
                           '_shareReqSeed','_PRODS','prodHubSub(2)','onboardingDone(3)',
                           'notifs(10)','topProds','emptyStates(3)','screenMark(12)',
                           'research(44)','skillVersions(7)',
                           '_PROPOSALS','_byKey','blankCust','_REQS','userRows',
                           'custScope(3)','createFlows(7)','login(2)','otpVerify','pdfOnly(21)',
                           'logout(2)','bridgeTag','subscribe','resFallback','iconTag',
                           'innerIcon','title(2)']  missed=[]
```

### Seam nào là dữ liệu, seam nào là nhãn giao diện

Prototype có **19 seam `_XXX()`**. Không phải cái nào cũng là dữ liệu — nối bừa cả
19 thì đi hỏi backend những thứ vốn là từ vựng giao diện. Phân loại:

**Dữ liệu, phải lấy từ DB:** `_CUST`, `_PROPOSALS`, `_KBS`, `_SKILLS`, `_TPLS`,
`_DIR`, `_REQS`, và mảng `userRows`. Tất cả đã nối.

**Nhãn/cấu hình giao diện, giữ tĩnh là đúng:** `_PERMS` (bốn mức quyền "Quản lý /
Chỉnh sửa / Bình luận / Chỉ xem"), `_PRIO` (chú giải High/Medium/Low kèm màu),
`_RSRC` (tên nhóm nguồn nghiên cứu), `_TESLIDES` (checklist slide khi test skill),
`_TOUR` (các bước hướng dẫn). Đây là từ vựng cố định của sản phẩm, không phải dữ
liệu người dùng.

**Suy ra từ state, không có nguồn riêng:** `_LIVECUST`, `_LIVEPROPS` (lọc bản đã
xoá khỏi `_CUST`/`_PROPOSALS` — nối gốc là đủ), `_EXPSEED` (dựng từ
`state.prop`), `_byKey` (index của `_CUST`).

**`_PRODS` — Product Knowledge:** ban đầu cố ý giữ tĩnh, nay đã nối vào DB.
`GET /products` được thêm tham số `?detail=true` để trả luôn
`pricing`/`capabilities`/`documents`/`positioning` — bridge lấy một lần trong
`loadAll()` thay vì gọi 6 lần chi tiết.

Map từ DB sang shape prototype: `tags` ← `capabilities` (thiếu thì
`targetIndustries`), `tech` ← `positioning`, `sources` ← `documents` hoặc
`sourceUrl`, `limits` ← `pricing`. Seed hiện có `pricing`/`documents` rỗng (chúng
đến từ luồng crawl), nên `limits` suy ra từ `packages` + `targetIndustries` để thẻ
sản phẩm không trống — vẫn là dữ liệu DB, không phải chữ cứng.

Phụ đề màn đó vốn là chữ cứng *“4 giải pháp ZBS… cập nhật 26/07/2026”*, sai ngay
khi DB có 6 sản phẩm. Đã thay bằng `prodHubSub` tự sinh từ `_PRODS()`: hiện
**“6 sản phẩm · Zalo Business Service · Zalo Cloud · nguồn: zalo.solutions”**.

**Còn lại, gắn với một khách hàng cụ thể:** `_CRM` (đoạn văn hồ sơ doanh nghiệp),
`_MATCH` (kết quả khớp giải pháp), `_LOCKED` (tài nguyên chưa có quyền xem). Ba
seam này không phải danh sách toàn cục mà là chi tiết **theo từng khách hàng**, nên
phải fetch theo `customerId` lúc mở hồ sơ chứ không nạp sẵn một lần như `loadAll()`.
Hiện **vẫn là mock** — đây là phần còn dở, xem §9.

### Nhận diện: favicon, title tab, icon Dashboard

**Favicon.** File bạn đưa đặt ở `src/frontend/assets/favicon.ico` (32×29, 32bpp,
3890 byte) — đúng vị trí đầu tiên mà `favicon_file()` dò. Route `/favicon.ico` trả
`image/x-icon`, byte khớp tuyệt đối với file gốc.

**Logo ZBS trước tiêu đề màn.** `ZBS.png` gốc là 935×870 PNG-RGBA 128 KB, viền trong
suốt dư khá nhiều. Đã crop về bbox `(53, 47, 897, 823)` rồi hạ xuống cao 128px —
`src/frontend/assets/zbs-mark.png`, 139×128, 7.7 KB — vì ảnh này nằm trên mọi màn,
để nguyên 128 KB cho một icon 22px là phí. `StaticFiles` mount ở `/` nên phục vụ sẵn
tại `/assets/zbs-mark.png`, không cần route mới.

`_patch_screen_mark()` chèn `<img>` vào **bên trong** div tiêu đề (12 chỗ: Dashboard,
Khách hàng, Proposal, Thông báo, Playbook & Skill, Template Library, Product Knowledge
Hub, và 5 màn con của Quản trị). Đặt bên trong chứ không đặt cạnh vì hai màn — Thông
báo và Product Knowledge Hub — bọc tiêu đề + phụ đề trong một div xếp dọc; ảnh để
ngoài sẽ rơi xuống dòng riêng thay vì đứng cạnh chữ. Kích thước bám cỡ chữ: 22px cho
tiêu đề 21px, 19px cho tiêu đề 18px của Product Knowledge Hub.

Có một chỗ ngầm: document bên trong standalone **đã có sẵn**
`<link rel="icon" type="image/x-icon" href="<uuid>">` trỏ vào resource favicon cũ
của bản Design. Thẻ đó thắng thẻ tôi chèn ở head ngoài, nên favicon mới sẽ không
hiện. Đã thêm patch `innerIcon` viết lại `href` của chính thẻ đó thành
`/favicon.ico`. Regex khớp cả khi có thuộc tính chen giữa (`type=...`) — lần đầu
tôi viết thiếu chỗ này nên patch trượt.

**Title tab.** Hai thẻ `<title>` (wrapper ngoài và document trong) đều là
"ZB Business Development"; patch `title(2)` đổi cả hai thành **`zBD Agent`** cho
khớp header trong app.

**Icon Dashboard.** `image55.png` không có trong bộ ảnh đã import. Đã vẽ mới bằng
pure Python: 162×162 RGBA (đúng khổ bộ icon còn lại), glyph trắng trên nền trong
suốt — cùng quy ước với 5 icon kia, vốn là **glyph trắng chỉ hiện rõ trên hàng
active nền `#0068FF`**, không có filter đổi màu. Hình là grid 2×2 widget, độ phủ
trắng đục 30.7% (image38 ~27%, image27 ~30%). CRC của cả 3 chunk đã verify.

### Clear toàn bộ dữ liệu khách hàng — và ba bug lộ ra nhờ đó

Đã xoá cả 9 khách hàng **qua chính API backend** (`DELETE /customers/{id}`, 9×204)
thay vì SQL thô, nên đi đúng đường phân quyền và có audit. Cascade dọn: 82 dữ kiện,
170 nguồn, 31 hoạt động, 2 bàn giao, 1 proposal, 10 job, 345 job_event, 5 bản export.

Ba lỗi thật chỉ lộ ra khi DB thực sự rỗng:

**1. DB rỗng làm app crash trắng.** `Root.renderVals(): Cannot read properties of
undefined (reading 'short')`. Nguồn: `const cust = this.state.cust || this._CUST()[0]`
và `_byKey()` trả `__src[0]`. Prototype luôn có mock nên `[0]` chưa bao giờ
`undefined`; nối DB thật rồi xoá hết là vỡ ngay. Đã thêm `_zbBlank()` — một object
khách hàng trung tính — làm chốt cuối cho cả hai chỗ.

> Lần vá đầu tôi mở đoạn chèn bằng **newline thật**, gây
> `Bad control character in string literal in JSON`: source component nằm trong một
> chuỗi JSON nên newline thô làm vỡ khâu unpack. Bỏ newline (JS không cần nó giữa
> hai method) là xong.

**2. Xoá khách hàng để lại bản ghi mồ côi.** `shares`, `share_links`,
`notifications`, `access_requests` **không có FK** tới `customers`, nên cascade
không chạm tới: còn lại 3 share, 1 share_link, 9 notification trỏ vào khách hàng đã
mất. Đã thêm `purge_links()` trong `services/shares.py`, gọi ở cả
`delete_customer` (kèm mọi proposal con) và `delete_proposal`, ghi số lượng đã dọn
vào audit meta.

**3. Share mồ côi không xoá được qua API.** `DELETE /shares/{id}` phải resolve
resource trước, resource đã mất nên trả **404** — rác không có lối ra. Tương tự,
`/notifications` lọc theo `user_id` nên notification của user khác cũng ngoài tầm.
Vì vậy thêm `app/maintenance.py`:

```
python -m app.maintenance orphans           # liệt kê (dry-run)
python -m app.maintenance orphans --apply   # xoá
python -m app.maintenance storage           # object MinIO không còn bản ghi DB
python -m app.maintenance storage --apply
```

Mặc định **luôn dry-run**, phải có `--apply` mới xoá. Đã dùng để dọn 13 bản ghi mồ
côi và 5 object export còn sót trên MinIO.

## 9g. Nhập skill từ file `.skill`

### Định dạng: bundle ZIP, không phải JSON

`.skill` là **ZIP** theo đúng quy ước Claude Skill — một thư mục gốc chứa
`SKILL.md` và (tuỳ chọn) `references/`:

```
zbd-research/
├── SKILL.md
└── references/
    ├── zbs-products.md
    └── industry-mapping.md
```

`SKILL.md` là Markdown mở đầu bằng frontmatter YAML; **thân Markdown chính là
prompt**:

```markdown
---
name: zbd-research
description: >
  zBD Agent — Customer Research Agent. Nghiên cứu toàn diện về một khách hàng
  theo đúng flow BD Zalo…
---

# zBD Agent — Customer Research

Bạn là Customer Research Agent trong hệ thống zBD Agent…
```

> Bản đầu tôi tự đặt format JSON phẳng — **sai**. File mẫu thật
> (`zbd-research.skill`) là ZIP bundle, nên đã viết lại parser theo đúng nó.

Frontmatter parse bằng **PyYAML**, không phải bộ đọc `key: value` tự viết: mô tả
trong file thật dùng *folded scalar* `>` trải nhiều dòng, bộ đọc thủ công sẽ cắt
sai. Khi nhập, description được gộp về một dòng.

Ngoài `name`/`description`, frontmatter nhận thêm `stage`, `scope`, `model`,
`temperature`, `version`, `inputSchema` nếu có; key lạ được giữ trong
`bundle.meta` và trả lại nguyên vẹn khi export. `stage`/`scope` nhận cả giá trị
tiếng Việt của enum và bí danh không dấu (`profile`→`Hồ sơ`,
`matching`→`Phương án`, `review`→`Kiểm duyệt`).

Vẫn nhận **một file `.md` rời** (SKILL.md không đóng ZIP) cho skill không có
references. Giới hạn: bundle 20 MB, tối đa 60 file, mỗi file 5 MB; bỏ qua
`__MACOSX/`, `.DS_Store`.

### Lưu ở đâu

- `SKILL.md` → `skills.prompt` + `description` + frontmatter đã parse
- `references/*` → **MinIO bucket riêng `zbd-skills`**, key
  `skills/{skill_id}/{đường dẫn tương đối}`
- Manifest → cột JSON mới **`skills.bundle`**
  (`{format, root, sourceFile, files[{path, objectKey, bytes}], meta}`)

Cố ý **không** dùng chung bucket `zbd-knowledge`: lệnh `maintenance storage` đối
chiếu object với `KnowledgeFile.object_key`, nếu trộn vào thì file reference của
skill sẽ bị coi là mồ côi và bị xoá. Bucket riêng nên lệnh đó cũng đã được dạy
đọc `skills.bundle`.

Migration `0669604fed00` thêm cột `bundle`. Autogenerate ra `nullable=False`
không default — sẽ vỡ nếu bảng đã có dữ liệu, nên đã sửa tay thành
`server_default '{}'::jsonb` rồi bỏ default ngay sau đó. `alembic check` xác nhận
không drift.

### Endpoint

| | |
|---|---|
| `POST /skills/import` | multipart nhiều file, kết quả **theo từng file** |
| `GET /skills/{id}/export` | dựng lại bundle ZIP từ DB + MinIO |

Import không all-or-nothing: file lỗi bị bỏ kèm lý do, file tốt vẫn tạo. Chỉ khi
**không** file nào nhập được mới rollback và trả 422. Trùng tên tự thêm hậu tố
`(2)`, `(3)`… kèm `renamed: true` — nhập lại cùng một file là chuyện thường.

Đã kiểm chứng với chính `zbd-research.skill`: nhập được, 2 file references lên
MinIO, `description` gộp đúng từ folded scalar, prompt 4910 ký tự. Export lại ra
ZIP cùng cấu trúc, hai file references **khớp byte**, nhập lại được.

### Xoá skill phải dọn cả MinIO

Cùng lớp bug với xoá khách hàng: `delete_skill` trước đây chỉ `session.delete()`,
để lại file references trên MinIO vĩnh viễn (chính lệnh `maintenance storage` phát
hiện ra). Nay `delete_skill` xoá object theo `bundle.files`, gọi `purge_links()`,
và ghi `attachmentsRemoved` vào audit. Đã kiểm chứng: import → 2 object → xoá
skill → 0 object.

### UI

Nút **“Nhập .skill”** nằm cạnh “+ Tạo skill”, chỉ hiện ở tab Skills
(`zbImportSkillShow`). Bấm là mở hộp chọn file thật (bridge tự tạo
`<input type="file" accept=".skill,.json,.md" multiple>`), nhập xong tự
`loadAll()` + `emit()` nên danh sách cập nhật ngay, không cần reload.

## 9g-bis. “Phiên bản” trong Skill: v2.3 / v2.1 là HTML cứng

Skill vừa import xong đã thấy `v2.3`, `v2.1` — **không phải lỗi đánh version**.
Backend hoàn toàn đúng: `update_skill()` chỉ bump khi `prompt` thật sự đổi
(`prompt_changed`), và DB đúng như vậy — cả 4 skill import đều `v1.0`, mỗi cái 1
bản ghi `skill_versions`, prompt nguyên vẹn 4.7k–7.8k ký tự.

Hai dòng đó là **markup viết cứng** trong khối “Phiên bản” của trình soạn skill,
kèm handler `skRestore23` / `skRestore21` gọi `_skRestore('v2.3')`. Ngày
`20/07/2026` ở header và dòng “hiện tại” cũng vậy, và tiêu đề hộp So sánh ghi
`v2.3 (02/07) → …`.

Nay 7 chỗ được nối vào dữ liệu thật (`skillVersions(7)`): timeline thành `sc-for`
trên `/skills/{id}/versions`, ngày lấy từ `updatedAt`, nút Khôi phục gọi
`POST /skills/{id}/versions/{ver}/restore`. Bridge cache theo `skillId` và `emit()`
sau khi nạp nên panel tự hiện, không cần reload.

### Bug nguy hiểm hơn phát hiện cùng lúc

`mapSkills()` **không map `prompt`**. Trình soạn đọc
`(this.state.skill || {}).prompt || '<boilerplate của prototype>'`, nên mở skill đã
import ra sẽ thấy **prompt mẫu của prototype**, không phải nội dung thật. Bấm “Lưu
skill” lúc đó sẽ gửi boilerplate lên → `prompt_changed` đúng → **ghi đè prompt thật
và bump version**. Đó chính là cơ chế sẽ tạo ra v1.1, v1.2… và làm mất nội dung
`.skill` đã nhập.

May là DB chưa bị hỏng (kiểm tra: 4/4 skill vẫn `v1.0`, prompt nguyên). Đã sửa bằng
`GET /skills?detail=true` (trả `prompt`/`model`/`temperature`/`inputSchema`) và map
đủ trong bridge. Kiểm chứng trên browser: mở `zbd-research` → textarea hiện đúng
4.912 ký tự bắt đầu bằng `# zBD Agent — Customer Research`, panel hiện
`v1.0 — hiện tại · 55 phút trước · khanhpp`, không còn v2.3/v2.1.

---

## 9g-ter. Icon và Tag của Skill / Knowledge Base

### Icon: cái bẫy “glyph trắng”

Icon card KB và Skill đều sinh từ `iconFor(i)` — **luân phiên** qua danh sách ảnh
pptx, nên mỗi card một icon khác nhau không theo nghĩa gì.

Khi gán một icon cố định mới phát hiện: các ảnh **palette** (`image17/18/27/36/47`…)
là **glyph trắng** dùng cho hàng nav nền `#0068FF` — đặt lên card trắng là **vô
hình**. Chỉ nhóm **RGBA** (`image102/103/104/105/108`) mới có màu, và cả 5 đều là
brand blue `#0068FF`. Đã chọn `image105` cho mọi skill (đúng ảnh mà card đầu tiên
đang hiện, tức icon bạn inspect).

Knowledge Base chuyển sang **glyph CSS** (trang giấy có gờ sách) thay vì ảnh. Giữ
đúng hình học được đưa — `13×16`, `border-radius:1.5px 3px 3px 1.5px`, spine bằng
hai `inset box-shadow` — nhưng **đổi hai màu trắng sang màu thương hiệu**: ô icon
nằm trên card trắng nên `rgba(255,255,255,.9)` sẽ không thấy gì.

```css
width:13px;height:16px;border-radius:1.5px 3px 3px 1.5px;
background:#0068FF;
box-shadow:inset 2.5px 0 0 rgba(255,255,255,.97),inset 3.5px 0 0 rgba(2,31,78,.5)
```

Cả hai đều là **style string do prop sinh** (`kb.iconStyle` / `sk.iconStyle`), nên
không phải sửa markup — chỉ đổi biểu thức trong props.

### Tag lấy từ Công cụ & nguồn dữ liệu, không phải stage

Tag trên card skill trước đây là `stage`. File `.skill` không khai `stage` nên cả 4
skill import đều rơi về default `Research` — bốn tag giống nhau, không mang tin.

“Công cụ & nguồn dữ liệu” trong trình soạn vốn là danh sách **4 mục viết cứng**,
toggle chỉ vào state cục bộ `seTools` và **mất khi rời màn**. Nay:

- Cột JSON mới **`skills.tools`** (migration `4417c8b592ca`, `server_default '[]'`)
- Frontmatter `.skill` nhận `tools:` với bí danh không dấu
  (`web search`/`crawl`/`internal kb`/`template` → `web_search`/`crawler`/`product_kb`/`template_library`),
  và `export` ghi lại bằng nhãn đọc được
- Panel toggle đọc từ `skill.tools` và ghi ngay qua `PATCH /skills/{id}`
- Tag = tools: 1–2 công cụ thì liệt kê (`Product KB · Template`), ≥3 thì gọn thành
  `3 công cụ`; chưa gán thì `Chưa gán công cụ`

Đã kiểm chứng: 4 skill ra tag `Product KB · Web Search`, `Product KB`,
`Product KB · Template`, `3 công cụ` — và **version vẫn `v1.0`** sau khi PATCH tools,
đúng như thiết kế (chỉ bump khi prompt đổi).

---

## 9g-quater. Template Library: chỉ upload PPTX / PDF

> Lỗi thời từ §9g-nonies: chỉ còn nhận PDF.

Màn này vốn có **hai** nút: `Upload PowerPoint` (`openUpTpl` — mở dialog upload giả
lập, có cả thanh phần trăm `kbUpPct` chạy cho vui) và `+ Tạo template`
(`openNewTpl` — điều hướng sang trình soạn `tpledit`, không lưu gì vào DB).

Nay bỏ hẳn nút tạo, giữ một nút chính **“+ Upload PPTX / PDF”** gọi
`POST /templates/import` thật. Chọn được nhiều file, upload lần lượt, báo kết quả
theo từng file; xong thì `loadAll()` + `emit()` nên danh sách hiện ngay.

### Backend nhận thêm PDF

`/templates/import` trước chỉ nhận `pptx`. Nay `TEMPLATE_EXTENSIONS` gồm cả `pdf`
với đúng `Content-Type` khi lưu MinIO, và `_parse_pdf()` (pypdf — có sẵn trong deps)
tạo một `TemplateSlide` cho **mỗi trang**, tiêu đề lấy từ dòng text đầu đọc được,
thiếu thì `Trang N`. PDF không có layout/placeholder như PPTX nên `layout` là
`pdf-page` và `placeholders` rỗng — ghi rõ để về sau không nhầm là thiếu dữ liệu.

Đã kiểm chứng:

| File | Kết quả |
|---|---|
| PPTX 5 slide | `slideCount=5`, tiêu đề đúng: Trang bìa · Executive Summary · Giải pháp đề xuất… |
| PDF 4 trang (font Unicode) | `slideCount=4`, tiêu đề tiếng Việt đúng dấu: Bìa proposal ZBS · Bối cảnh khách hàng… |
| DOCX | từ chối — `FILE_TYPE: Chỉ nhận PPTX, PDF.` |

> Lần test đầu tiêu đề PDF ra `B■i c■nh` — đó là lỗi **file test của tôi** (dùng
> Helvetica không có glyph tiếng Việt khi tạo PDF), không phải lỗi parser. Tạo lại
> bằng font Unicode từ `app.workers.fonts.unicode_font()` là ra đúng dấu.

Xoá template cũng đã dọn file trên MinIO — kiểm tra sau khi xoá: `zbd-templates` còn
**0 object**.

### Đã bỏ luôn đường “lưu proposal thành template”

Để nhất quán “template chỉ đến từ upload”, đường thứ hai cũng đã gỡ. Nó nằm ở
`finalizeProp` — khi đánh dấu proposal hoàn tất thì mở dialog hỏi lưu thành
template (`saveTpl: true`) rồi `stSave` gọi `POST /templates`.

Chặn ngay ở `finalizeProp` khiến **cả dialog lẫn `stSave` không thể tới được**, nên
không cần vá từng chỗ bên trong. Đồng thời gỡ `ZB.createTpl` khỏi bridge để không
còn hàm nào tạo template ngoài upload.

Đã kiểm chứng bằng cách chặn `window.fetch`: đi hết màn Template Library,
**không có `POST /templates` nào** — chỉ còn `POST /templates/import`.

| Đường tạo template | Trạng thái |
|---|---|
| `+ Upload PPTX / PDF` → `POST /templates/import` | **đường duy nhất** |
| `+ Tạo template` → `tpledit` | đã bỏ |
| `finalizeProp` → dialog → `stSave` → `POST /templates` | đã bỏ |
| `ZB.createTpl` | đã gỡ khỏi bridge |

`POST /templates` vẫn còn ở backend (API công khai, dùng được từ script/tích hợp),
chỉ là frontend không còn gọi.

---

## 9g-quinquies. Template: detect metadata, thumbnail trang đầu, preview

> Phần PPTX trong mục này lỗi thời từ §9g-nonies.

### Phát hiện khi upload — `app/services/deckmeta.py`

Upload xong là đọc luôn từ file, lưu vào cột `proposal_templates.theme`:

| | PPTX | PDF |
|---|---|---|
| Tỷ lệ | `p:sldSz cx/cy` | kích thước trang 1 |
| Bảng màu | `theme1.xml` `<a:srgbClr>` (colorScheme) | màu span text 6 trang đầu |
| Font | `<a:fontScheme>` typeface | font span, bỏ tiền tố subset `ABCDEF+` |
| Ngôn ngữ | text `<a:t>` toàn deck | `page.get_text()` 6 trang đầu |

Ngôn ngữ đoán bằng mật độ dấu tiếng Việt + từ khoá (`của`, `giải pháp`…), ngưỡng
12 dấu hoặc 3 từ. Không dùng thư viện detect ngôn ngữ vì chỉ cần phân biệt VI/EN.

Font PDF lọc tên nội bộ dạng `Type3 (1031 0 R)`, `F12` — đó là tên object trong
file, không phải font người thiết kế chọn.

### Thumbnail = trang/slide đầu

- **PDF**: render thật bằng PyMuPDF (`pymupdf` mới thêm — wheel độc lập, không cần
  poppler/LibreOffice), scale về ngang 640px.
- **PPTX**: không nhúng `docProps/thumbnail` và render slide cần LibreOffice. Dùng
  heuristic lấy **ảnh lớn nhất mà slide 1 tham chiếu** (deck proposal hầu như luôn
  có bìa full-bleed). File Jollibee vẽ bìa bằng shape nên không có ảnh → fallback
  `synth_cover()` sinh bìa từ **chính bảng màu phát hiện được của file đó**, không
  phải ảnh mock.

Lưu ở `zbd-templates` cùng file gốc, phục vụ qua `GET /templates/{id}/thumbnail`.

**Cái bẫy auth**: `background:url(...)` trong CSS **không gửi được header
Authorization** → route thumbnail trả 401 và bìa trắng. Đã sửa ở bridge: fetch có
Bearer rồi `URL.createObjectURL` → blob URL, đúng cách prototype vẫn dùng cho ảnh.

Prototype vẽ tên + dòng meta **đè lên** bìa; với ảnh thật thì chữ trắng chìm mất,
nên bìa được xếp thêm lớp scrim `linear-gradient(rgba(2,31,78,.12) → .78)`.

### Preview file gốc

`GET /templates/{id}/file` trả presigned URL MinIO (TTL 3600s). Nút “Xem trước”
trong trình soạn nay mở file gốc ở tab mới thay vì bật panel mock.

### Đã bỏ hai khối

`Cấu trúc proposal` và `Áp dụng mặc định` cắt trọn card bằng `_remove_card()` —
đếm thẻ `<div>`/`<\u002Fdiv>` để tìm đúng biên.

> Lần đầu tôi khớp card bằng chuỗi `padding:18px`, nhưng “Cấu trúc proposal” dùng
> `padding:20px` nên `rfind` nhảy qua nó và **cắt sai một card khác cách 11.795 ký
> tự**. Phát hiện nhờ so `div` mở/đóng: baseline lệch 3, bản vá cũng phải lệch đúng
> 3 mới là cắt sạch. Đã đổi sang khớp tiền tố `…border-radius:12px;padding:`.

### Kết quả trên 3 file thật

| Template | Loại | Tỷ lệ | Ngôn ngữ | Font | Thumbnail |
|---|---|---|---|---|---|
| Jollibee_x_ZBS_Proposal | PPTX | 16:9 | VI | Calibri Light… | sinh từ palette |
| Vietcombank x ZBS | PDF | 16:9 | VI | Gilroy, Roboto, Inter | trang 1 thật |
| ZBS_Giải pháp cho Napas | PDF | 16:9 | **EN** | Gilroy, Roboto | trang 1 thật |

Napas ra `EN` là **đúng**: các trang đầu deck đó viết tiếng Anh (“Member Engagement
Platform for NAPAS”). Bảng màu hai PDF ra đúng màu thương hiệu Zalo — `#0068FF`,
`#1A30A0`, `#021F4E`.

---

## 9g-sexies. Phiên bản Template, session 6 tiếng, giới hạn preview PPTX

> Phần PPTX trong mục này lỗi thời từ §9g-nonies.

### Panel “Phiên bản” của Template cũng là HTML cứng

Lần trước tôi báo “không tìm thấy dòng version cứng trong tpledit” — **sai**, do bộ
lọc tìm kiếm của tôi chỉ soi cửa sổ 700 ký tự sau nhãn, mà `v1.2` nằm xa hơn. Panel
thực tế có hai dòng cứng `v2.0 — hiện tại · 18/07/2026 · thêm slide Bảng giá` và
`v1.2 · 30/06/2026`, cộng hộp “So sánh phiên bản” ghi `v1.2 (30/06) → v2.0 hiện tại`
và nút `Khôi phục v1.2`.

Nay cả 5 chỗ lấy từ `/templates/{id}/versions`, nút Khôi phục gọi
`POST /templates/{id}/versions/{ver}/restore` (`tplVersions(5)`).

### `check_balance` đã cứu đúng một lần

Vá `tplRestore` tôi lại mở `{` cho thân arrow function trong khi đuôi gốc `})` chỉ
đóng `_confirm(`. Guard chặn ngay lúc build: `{} lệch: gốc 2 -> sau vá 3` — thay vì
để thành SyntaxError trắng màn. Đổi sang dạng biểu thức (IIFE ternary) là xong.

### Session 6 tiếng và reload không phải login lại

`ACCESS_TOKEN_TTL_MINUTES` 60 → **360**. Nhưng đó **không phải** nguyên nhân chính
của việc reload bắt login lại: prototype đọc màn hình bằng
`this.state.screen ?? this.props.startScreen ?? 'login'`, tức `state.screen` khởi
tạo là `undefined` và luôn rơi về `login` bất kể token còn hạn.

Nay callback `subscribe` kiểm tra đúng biểu thức đó: token còn hạn + `/me` thành
công thì tự vào `dashboard` (hoặc `onboarding` nếu `needsOnboarding`).

> Lần vá đầu tôi so `this.state.screen === 'login'` — không bao giờ khớp vì giá trị
> là `undefined`. Phải mirror nguyên biểu thức `?? ... ?? 'login'`.

Đã kiểm chứng: đăng nhập → reload → vào thẳng Dashboard “khanhpp”, token giữ nguyên,
`expiresAt` cách hiện tại **6.0 tiếng**.

### Nút Reload

Thêm nút `Reload` cạnh `+ Upload PPTX / PDF`, gọi `ZB.loadAll()` rồi toast “Đã tải
lại danh sách template”.

### PPTX không preview được — và vì sao

Browser render inline được PDF, **không render được PPTX**; `window.open` một URL
`.pptx` chỉ tải file về. Không có đường nào thuần frontend giải quyết.

Cách sửa đúng là convert PPTX → PDF phía server, nhưng cần **LibreOffice** —
máy hiện tại không có (`which soffice` rỗng), và nó cũng không phải wheel Python nên
`uv add` không giải quyết được.

Tạm thời: `openTplPrev` xét `kind`. PDF → mở file gốc tab mới. PPTX → mở panel danh
sách slide dựng từ bảng `template_slides` (đã có title/layout thật từ lúc parse).

Muốn PPTX preview đúng như PDF thì cài `brew install --cask libreoffice` (hoặc thêm
`libreoffice` vào image Docker), rồi convert lúc upload — làm vậy đồng thời có
**thumbnail slide 1 thật** thay cho bìa sinh từ palette.

### Import template chưa chống trùng

Upload lại cùng một file tạo bản mới, không dedupe. Hiện có 7 template trong đó
`Jollibee_x_ZBS_Proposal` ×3 và `Vietcombank` ×2. Skill đã có `_unique_name()` + đổi
tên `(2)`, template thì chưa — nên dùng `content_hash` như `knowledge_files` là hợp
lý nhất (nhận diện đúng file trùng, không chỉ trùng tên).

---

## 9g-septies. Dedupe template và convert PPTX → PDF

> Nhánh convert trong mục này đã bị xoá — xem §9g-nonies.

### Dedupe theo nội dung, không theo tên

Cột mới `proposal_templates.content_hash` (SHA-256, có index) — migration
`7b4b7653be80`, kèm `preview_key`. Import tính hash trước khi tạo; trùng thì trả
bản đã có kèm `deduplicated: true`, **không tạo bản mới**.

Chọn hash chứ không chọn tên như skill (`_unique_name` → `(2)`): hai file khác nội
dung có thể trùng tên, và cùng một file có thể được đổi tên. Hash nhận diện đúng thứ
cần chặn. Cùng cách `knowledge_files` đang dùng.

Đã backfill hash cho 7 bản đang có, phát hiện `Jollibee_x_ZBS_Proposal.pptx` ×3 và
`Vietcombank…pdf` ×2 (cùng `size_bytes`, cùng hash). Xoá 3 bản dư **qua API** để có
audit và tự dọn file MinIO — còn lại 4 bản duy nhất. Upload lại Jollibee sau đó trả
`deduplicated: true`, tổng vẫn 4.

### PPTX → PDF: `app/services/office.py`

`soffice_path()` dò LibreOffice ở 6 vị trí thường gặp (PATH, `/Applications`,
`/opt/homebrew`). Không có thì `pptx_to_pdf()` trả `None` — **chủ ý không raise**, vì
upload template vẫn phải thành công dù thiếu bản preview.

Convert chạy headless với `-env:UserInstallation` trỏ vào thư mục tạm riêng, tránh
đụng profile người dùng và tránh treo khi có instance LibreOffice khác đang mở.
Timeout 180s.

Khi convert được, upload PPTX sẽ:
1. lưu `preview.pdf` cạnh file gốc → `preview_key`
2. render **slide 1 thật** từ PDF đó làm thumbnail, thay bìa sinh từ palette
3. đặt `theme.previewKind = "pdf"`, `convertedFrom = "pptx"`

`GET /templates/{id}/file` trả `preview_key` khi xem (browser render inline được) và
`object_key` khi `?download=true` (đưa đúng file gốc). Response thêm
`previewKind` + `inlineViewable` để frontend biết mở tab mới hay dùng panel slide.

### Máy hiện tại chưa có LibreOffice

`brew install --cask libreoffice` cần quyền cài phần mềm hệ thống nên tôi không chạy
được — bạn tự chạy. Code đã viết sẵn để **tự bật ngay khi có** `soffice`, không cần
sửa gì thêm.

Vì dedupe chặn tạo bản mới, upload lại cùng file sau khi cài sẽ **không** tạo bản
trùng mà đi vào nhánh bù: nếu template đó còn thiếu `preview_key` và LibreOffice đã
có, nó convert, lưu preview, render lại thumbnail rồi trả `previewBackfilled: true`.

---

## 9g-octies. Preview PDF/PPTX ngay trong webapp

> Phần PPTX trong mục này lỗi thời từ §9g-nonies.

### Render trang thành ảnh, không mở tab mới

Ban đầu tôi cho “Xem trước” gọi `window.open` sang presigned URL — PDF thì browser
render được nhưng **mở tab mới**, không phải trong webapp. Nay panel “Xem trước” có
sẵn của prototype hiển thị **ảnh từng trang** render từ file thật.

`GET /templates/{id}/pages` trả số trang + `renderable`; `GET /…/pages/{index}` render
trang đó bằng PyMuPDF ở 1280px và **cache vào MinIO** (`templates/{id}/pages/{n}.png`).
Render theo yêu cầu chứ không lúc upload — đo được: lần đầu ~350 KB, lần hai lấy từ
cache **0.008s**.

Ảnh trang cũng cần Bearer nên bridge tải bằng fetch rồi `createObjectURL`, và **chỉ
tải trang đang xem** — không nạp cả deck 25 trang.

### Không vỡ tỷ lệ slide

Lần đầu tôi hardcode `aspect-ratio:16/9` **và** giữ `min-height:236px` — hai thứ chèn
nhau làm hộp cao hơn tỷ lệ thật. Nay `aspect-ratio` lấy từ `theme.ratioExact` của
chính file (ví dụ 1.778), bỏ `min-height`, và `background-size:contain` nên ảnh không
bao giờ bị kéo méo dù deck là 16:9, 4:3 hay A4.

### PPTX: nói thật thay vì vẽ slide giả

Panel vốn vẽ slide mô phỏng (badge + tiêu đề + khối xám + câu “Nội dung sẽ do AI
sinh…”). Với PPTX chưa convert được, nó hiện **nội dung bịa trông như thật** — tệ hơn
là không hiện gì. Nay khi template là file đã upload mà chưa render được, panel bỏ
hết chữ mock và ghi rõ:

> Chưa xem trước được file này — Cài LibreOffice rồi upload lại file này là xem được.

Số slide vẫn lấy đúng từ `template_slides` trong DB (22), không dùng `rows.length`
của mock.

### Trạng thái đo được

| Template | Loại | Preview trong app |
|---|---|---|
| ZBS_Giải pháp cho Napas | PDF | **Slide 1/21, ảnh thật** |
| Vietcombank x ZBS | PDF | 25 trang, render được |
| FnB - ZBS Introuce… | PDF | 20 trang, render được |
| Jollibee_x_ZBS_Proposal | PPTX | báo cần LibreOffice |

PPTX sẽ tự chạy như PDF ngay khi cài LibreOffice — upload lại file đó sẽ đi vào nhánh
`previewBackfilled` (§9g-septies), convert sang PDF rồi render trang bình thường.

---

## 9g-nonies. Proposal chỉ còn PDF — bỏ hẳn PPTX

Quyết định của người dùng sau khi thử hết các đường render PPTX. Ghi lại **bằng chứng
đo được**, vì đây là lý do bỏ:

| Đường | Kết quả thật đã thử |
|---|---|
| `python-pptx` | đọc/ghi được cấu trúc, **không render** — không phải engine vẽ |
| `aspose-slides` 26.7.0 | cài được arm64, đọc đúng 22 slide 960×540, nhưng render `DllNotFoundException: libgdiplus`; PDF ra 17 byte |
| `Spire.Presentation.Free` | **không có wheel arm64** (chỉ manylinux1_x86_64, macosx_10_7_universal, win_amd64) |
| `Spire.Presentation` 11.6.2 (trial) | convert được, PDF 267.102 byte, chữ tiếng Việt và màu đúng — nhưng **10/22 trang** và **watermark “Evaluation Warning” mọi trang** |
| LibreOffice / gotenberg | chạy đúng, nhưng là **phần mềm hệ thống**, phải cài thêm hoặc thêm container |

Không có đường thuần Python, miễn phí, không phụ thuộc hệ thống nào cho ra bản
render đúng. Nên PPTX bị loại khỏi vòng đời proposal thay vì để một tính năng
“có mà không dùng được”.

### Đã xoá

| Chỗ | Trước | Nay |
|---|---|---|
| `app/services/office.py` | dò LibreOffice, `pptx_to_pdf()`, 3 đường convert | **xoá cả file** |
| `docker-compose.yml` | có service `gotenberg` | xoá |
| `.env` | `PPTX_CONVERTER`, `GOTENBERG_URL`, `GOTENBERG_PORT` | xoá |
| `config.py` | `pptx_converter`, `gotenberg_url` | xoá |
| `deckmeta.py` | `from_pptx`, `_pptx_cover`, `synth_cover`, `detect` | xoá — chỉ còn `from_pdf` |
| `templates.py` | `TEMPLATE_EXTENSIONS` có `pptx`, `_parse_pptx`, nhánh convert, nhánh `previewBackfilled` | `{"pdf": "application/pdf"}` |
| `ExportFormat` | `PPTX`, `PDF` | chỉ `PDF` |
| `workers/export.py` | `_build_pptx` (~5.3 KB) + `_text_box` | xoá; `_build_pdf` là đường duy nhất |
| `ExportRequest.format` | mặc định `PPTX` | mặc định `PDF` |

`python-pptx` **vẫn giữ** trong `pyproject.toml`: Knowledge Base còn bóc text từ
tài liệu `.pptx` khách gửi (`workers/knowledge.py`). Đó là đọc nội dung, không phải
render — không liên quan tới proposal.

### Bù lại một việc: PDF export ăn theo bảng màu template

`template` được đọc ra trong worker nhưng trước đây chỉ `_build_pptx` dùng. Bỏ PPTX
là nó thành biến chết. Thay vì xoá, `_build_pdf(proposal, customer, slides, template)`
lấy `theme.palette` đã detect được: màu tối nhất (tổng RGB < 400) làm màu tiêu đề,
màu sáng còn lại làm màu phụ đề — không có palette thì về NAVY/BLUE mặc định.

Đo thật: template `Jollibee_x_ZBS_Proposal` (palette `#44546A #E7E6E6 #4472C4 #ED7D31`)
→ PDF **35.935 byte, 3 trang**, tiếng Việt đúng dấu.

### Chữ trong prototype: seam `pdfOnly` — 22 chỗ

Prototype ghi cứng “PowerPoint”/“PPTX” rải rác, và nhãn hộp kết xuất suy ra từ
`expFmt === 0 ? 'PPTX' : 'PDF'`. Nếu chỉ bỏ lựa chọn PPTX khỏi `expFormats` thì
**lựa chọn duy nhất còn lại vẫn hiện chữ “PPTX”** (index 0). Nên seam thay cả biểu
thức, không chỉ mảng:

| Chỗ | Trước | Sau |
|---|---|---|
| nút Template Library | `+ Upload PPTX / PDF` | `+ Upload PDF` |
| tiêu đề dialog upload | `Upload PowerPoint` | `Upload PDF` |
| phụ đề dialog | `File .pptx sẽ được tách bố cục…` | `File PDF sẽ được tách từng trang…` |
| `expFormats` | 2 lựa chọn | 1 lựa chọn PDF |
| `expFmtLabel`, `expRunLabel`, `expFailTitle`, `expFileName`, `expFileMeta`, toast tải về | ternary theo `expFmt` | `'PDF'` |
| `_EXPSEED()` | dựng sẵn 2–3 file đã xuất kèm dung lượng bịa | trả `[]` |
| tour, quyền, thẻ hướng dẫn | “xuất PowerPoint”, “Xuất PowerPoint / PDF” | “xuất PDF”, “Xuất proposal PDF” |
| `auditDetRows` fallback | `xuất PPTX proposal` | `xuất PDF proposal` |
| body preview khi không render được | “Cài LibreOffice rồi upload lại…” | “Không render được trang từ file này — upload lại bản PDF.” |

Báo cáo vá: **41/41 seam applied, 0 missed** (`pdfOnly(22)`).

### Đã kiểm chứng qua HTTP

| Việc | Kết quả |
|---|---|
| upload `.pptx` | `FILE_TYPE: Định dạng không được hỗ trợ. Chỉ nhận PDF.` — `allowed: ["pdf"]` |
| upload lại PDF đã có | `deduplicated: true`, giữ `tpl_4853fbca49`, `slideCount 21` |
| `GET /templates/{pdf}/pages` | `total 21, renderable true` |
| `GET …/pages/1` | HTTP 200, `image/png`, 66.133 byte |
| `GET /templates/{pptx cũ}/pages` | `renderable false`, `reason: "Bản upload cũ định dạng PPTX — hệ thống chỉ hỗ trợ PDF"` |
| `openapi.json` | `ExportFormat: ["pdf"]`, summary “Kết xuất PDF”, “Nhập template từ PDF” |

### Đã xoá bản PPTX còn lại — và một lỗ rác lộ ra khi xoá

`tpl_2e925e61c0 · Jollibee_x_ZBS_Proposal.pptx` (22 slide) đã xoá theo yêu cầu.
Nhưng `delete_template` khi đó chỉ `storage.delete(object_key)` — tức **bỏ sót** bìa
(`theme.coverKey`), `preview.pdf` và cache `pages/N.png`; và không gỡ share/link như
`delete_skill` vẫn làm. Xoá một template là để lại rác trong MinIO cùng share trỏ vào
hư không.

Đã sửa:

- `storage.delete_prefix(bucket, prefix)` — liệt kê rồi xoá mọi object dưới prefix.
  Một template giữ nhiều key nên xoá lẻ từng key là sót.
- `delete_template` xoá cả `templates/{id}/` và gọi `purge_links(TEMPLATE, id)`,
  ghi số lượng vào `meta` của audit để kiểm lại được.

Đo thật khi xoá: MinIO có 2 object (`.pptx` 607.884 B + `cover.png` 6.660 B) →
sau xoá prefix **sạch**; `template_slides` 0, `template_versions` 0 (cascade);
audit ghi `khanhpp xoá template Jollibee_x_ZBS_Proposal` với
`meta = {"files": 2, "shares": 0, "shareLinks": 0, "notifications": 0, "accessRequests": 0}`.
UI còn **3 template, tất cả `kind = pdf`**, không còn chữ PPTX nào hiển thị.

---

## 9g-decies. Điền sẵn email đăng nhập + luôn chạy luồng lần đầu

Hai thứ tách rời nhau, cùng điều khiển bằng env nên đổi được mà không sửa code.

### `LOGIN_DEFAULT_EMAIL` — điền sẵn ô email

Prototype ghi cứng `email: 'khanhpp@vng.com.vn'` trong state khởi tạo — email của
người dựng bản mẫu. Seam `loginEmail` thay bằng `settings.login_default_email`
(mặc định `zaloadmin@vng.com.vn`); để trống env là không điền sẵn. Vá lúc serve nên
file prototype của người dùng không bị sửa.

### `ALWAYS_ONBOARDING_EMAILS` — luồng lần đầu chạy lại mọi lần

`needs_onboarding` trước đây chỉ xét `onboarding_steps` đã đủ chưa, nên xem xong
một lần là **không xem lại được** — muốn kiểm tra lại luồng phải xoá tay cột đó
trong DB. Thêm `User.always_onboarding`: email nằm trong danh sách thì
`needs_onboarding` và `needs_tour` **luôn** trả `True`.

Đặt ở property của model, không ở route, vì có **ba** nơi cùng phát cờ này
(`/auth/otp/request` khi bypass OTP, `/me`, `/me/onboarding`) — sửa một chỗ thì cả
ba nhất quán.

Không khoá người dùng trong vòng lặp: `ZB.completeOnboarding()` hạ
`ZB.me.needsOnboarding` ở phía client, nên trong một phiên vẫn đi tiếp vào dashboard
bình thường; reload mới gặp lại. Tiến độ **vẫn ghi vào DB** (`onboarding_steps`,
`tour_completed`), nên bỏ email khỏi env là hành vi về đúng bình thường ngay.

### Đo thật

| Việc | Kết quả |
|---|---|
| màn login (localStorage đã xoá) | ô email = `zaloadmin@vng.com.vn`, placeholder giữ nguyên |
| bấm “Nhận mã OTP” | zaloadmin nằm trong `OTP_BYPASS_EMAILS` → `direct: true`, vào thẳng |
| màn tiếp theo | `Bước 1/3 · Chào mừng đến với zBD Agent của ZBS` |
| `PATCH /me/onboarding` đủ 3 step + `tourCompleted: true` | trả về `needsOnboarding: true, needsTour: true` |
| `GET /me/onboarding` sau đó | `completedSteps` đủ 3, `tourCompleted: true`, vẫn `needsOnboarding: true` |
| bấm “Bỏ qua” | vào app bình thường, không lặp lại |
| reload | onboarding hiện lại từ `Bước 1/3` |
| khanhpp (không trong danh sách) | `needsOnboarding: false, needsTour: false` — không ảnh hưởng |

Một cái bẫy khi tự thử bằng curl: field của `PATCH /me/onboarding` là
**`completedSteps`**, không phải `steps`. Gửi sai tên thì Payload chặn cả body,
`tour_completed` không được ghi mà nhìn như đã lưu.

---

## 9g-undecies. Isolate theo user: khách hàng, skill, KB, template

Yêu cầu: mỗi user chỉ thấy dữ liệu **của mình**, cộng thứ **được share**, cộng thứ
**dùng chung** (`scope = Shared`). Bộ máy phân quyền đã có sẵn (`Share`, `ShareLink`,
`Scope`, `resolve`, `visible_filter`) nhưng bị vô hiệu ở **hai chỗ**:

### Chỗ hổng 1: quyền admin vượt qua mọi thứ

`resolve()`, `visible_filter()` và `apply_visibility()` đều mở toàn bộ khi
`user.is_admin`. Mà cả 4 user khởi tạo đều nằm trong `BOOTSTRAP_ADMIN_EMAILS` → **ai
cũng là admin → ai cũng thấy hết**. Isolation trên giấy, không có thật.

Thêm `admin_override(user)` = `is_admin and settings.admin_sees_all_resources`, mặc
định `ADMIN_SEES_ALL_RESOURCES=false`. Ba nhánh cùng gọi một hàm nên không lệch nhau.
Mục Quản trị (user, audit, policy) đi qua `AdminUser` ở `deps.py`, **không** phụ thuộc
cờ này — admin vẫn quản trị được, chỉ không đọc ké dữ liệu người khác.

### Chỗ hổng 2: ba list của Library không lọc gì cả

`/customers` và `/proposals` đã gọi `apply_visibility`. Nhưng `/skills`,
`/knowledge-bases`, `/templates` thì **trả về mọi bản ghi**, chỉ gắn cờ
`locked: true` cho thứ không có quyền — tức tên, phiên bản, số slide, chủ sở hữu của
người khác vẫn lộ. `/search` cũng vậy với 3 loại này.

Nay cả 6 chỗ dùng `apply_visibility`. Hàng không thuộc phạm vi thì **không có trong
kết quả**, không phải hiện rồi khoá.

Thêm `is_builtin` vào `visible_filter`: nội dung hệ thống ship kèm là dùng chung, nếu
không nó sẽ vô hình với tất cả (owner NULL + scope Mine).

### Điều kiện thấy được một resource

| Đường | Nguồn |
|---|---|
| sở hữu | `owner_id == user.id` |
| được share riêng | `Share.user_id == user.id` |
| share theo nhóm | `Share.team_id == user.team_id` |
| link toàn công ty | `ShareLink.mode = COMPANY` (phải bật trong `SystemPolicy`) |
| dùng chung | `scope == Shared` (và `restricted` false nếu model có cột đó) |
| hệ thống | `is_builtin == true` |

Khách hàng **không có cột `scope`** — nên không có kiểu "khách hàng dùng chung", chỉ
share tường minh. Đúng ý: hồ sơ khách hàng là dữ liệu riêng của người phụ trách.
`Product` (Product Knowledge) vẫn global — đó là danh mục sản phẩm Zalo, không phải
dữ liệu người dùng.

### Đo thật qua HTTP, 3 user

| | khách hàng | skill | KB | template |
|---|---|---|---|---|
| khanhpp (chủ hầu hết dữ liệu) | 1 `vietmoney` | 4 | 1 | 3 |
| zaloadmin | 1 `Nhà thuốc Long Châu` | 0 | 1 | 0 |
| lanlt2 | 0 | 0 | 1 | 0 |

KB `Case study khách hàng Việt Nam` là `scope = Shared` nên cả 3 đều thấy — đúng
nghĩa dùng chung. `/search?q=zbd` trả 4 skill cho khanhpp và **rỗng** cho hai người
kia. Dashboard cũng theo đúng phạm vi (`customersOwned` 1 / 1 / 0).

Mở trực tiếp bằng id đều bị chặn, kể cả các route con:

| Request của zaloadmin | Kết quả |
|---|---|
| `GET /skills/{id của khanhpp}` | 403 `Bạn không có quyền view với skill này.` |
| `GET /skills/{id}/export` | 403 |
| `GET /templates/{id}` · `/file` · `/pages` | 403 |

Share rồi thì đúng mức quyền, không hơn:

| Việc | Kết quả |
|---|---|
| khanhpp `POST /shares` skill cho zaloadmin, `view` | 201 |
| zaloadmin `GET /skills` | thấy skill đó, `locked: false` |
| zaloadmin `GET /skills/{id}` | 200 |
| zaloadmin `PATCH /skills/{id}` | **403** — view không được sửa |
| khanhpp `PATCH /templates/{id}` scope `Shared` | zaloadmin **và** lanlt2 đều thấy ngay |

Dữ liệu thử đã dọn: gỡ share, đưa template về `Mine`, xoá notification của share đó.

---

## 9g-duodecies. Deep Research fail: ba nguyên nhân khác nhau

Test bằng Playwright trên khách hàng thật (`vietmoney`) và đọc lại job đã fail trong
DB. Không phải một lỗi — là **ba** lỗi độc lập, hai trong số đó ở phía MaaS.

### 1. Sinh quá dài → gateway cắt bằng 502

Hai job fail lúc 13:42 và 14:34 đều dừng ở bước `extract`:

| Job | extract bắt đầu | báo lỗi | khoảng cách |
|---|---|---|---|
| `job_0bf3a8c698` | 13:49:18 | 14:04:31 | **913s** |
| `job_9ad94f60f9` | 14:38:58 | 14:54:05 | **907s** |

Cả hai ~15 phút rồi `502 An invalid response was received from the upstream server`.
Client timeout đặt 1200s nên **gateway cắt trước**, và thông báo 502 không nói gì về
nguyên nhân thật.

Đã loại giả thuyết "payload quá lớn" bằng đo trực tiếp: prompt 300KB → OK 18,6s
(`in=87.043`), prompt 700KB → OK 25,8s (`in=203.043`). Kích thước đầu vào không phải
vấn đề. Vấn đề là `research.extract` đặt `max_tokens=65_536` cho một model reasoning:
nó sinh liên tục tới khi hết trần.

| Tham số | Trước | Sau |
|---|---|---|
| `max_tokens` | 65.536 | 16.384 |
| `input_share` | 0,80 (≈1,75M ký tự) | 0,10 (≈235K ký tự) |
| `timeout` | 1.200s | 720s — **dưới** ngưỡng gateway để lỗi hiện đúng bản chất |

Thêm `EXTRACT_ATTEMPTS = ((1.0, 16_384), (0.45, 8_192))`: lần 2 rút ngắn ngữ liệu và
hạ trần sinh, kèm bước UI “Ngữ liệu quá dài — đang rút gọn rồi thử lại”.

### 2. Pool model sập → 503 no_available_workers

Chạy lại thì fail **kiểu khác**, chỉ sau 6 giây:

```
MaaS trả về 503: {"error":{"type":"Service Unavailable",
 "code":"no_available_workers","message":"No available workers (all circuits open or unhealthy)"}}
```

Và sau đó là `500 call_upstream_request_error — error sending request for url
(http://glm-52-fp8-h200-8x-worker-service.default.svc.cluster.local:8889/...)`. Tức
worker service của **đúng model đang cấu hình** (`z-ai/glm-5.2`) không truy cập được.

Dò `GET /models` thấy 38 model, thử từng model cùng một prompt JSON:

| Model | Kết quả |
|---|---|
| `z-ai/glm-5.2` (đang dùng) | **503 / 500** |
| `gemini/gemini-3.1-flash-lite` | OK — JSON hợp lệ |
| `qwen/qwen3.7-plus` | OK 25,3s — JSON hợp lệ |
| `gemini/gemini-3-flash-preview` | OK 3,3s — JSON hợp lệ |
| `deepseek/deepseek-v4-flash` | OK 2,0s — JSON hợp lệ |
| `minimax/minimax-m2.5` | trả lời nhưng chèn `<think>` → không dùng cho JSON |
| `z-ai/glm-5.1`, `openai/gpt-5`, `deepseek/deepseek-v4-pro`, `qwen/qwen3-235b-*` | 404 không có model |

Nên vấn đề không phải MaaS chết, mà là **một model chết trong khi cả sàn vẫn chạy** —
và client không có đường vòng.

`maas.py` trước đây bọc `complete()` bằng `tenacity`: 3 lần thử, **cùng một model**,
cho **mọi** `MaasError` — vô ích khi pool sập, mà 404/400 cũng bị thử lại vô nghĩa.
Thay bằng:

- `TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}` — chỉ những mã này mới
  thử lại; 400/401/403/404/422 raise ngay.
- `_attempt_plan(model)`: thử model chính 2 lần (cho cú chớp nhoáng) rồi lần lượt hạ
  sang `MAAS_FALLBACK_MODELS`, chờ theo `MAAS_RETRY_DELAYS`.
- Log `maas_fallback_used(wanted, served_by)` và `Completion.model` ghi model đã thực
  sự phục vụ — không âm thầm đổi model mà không ai biết.

`.env`:

```
MAAS_FALLBACK_MODELS=gemini/gemini-3.1-flash-lite,qwen/qwen3.7-plus,gemini/gemini-3-flash-preview
MAAS_RETRY_DELAYS=2,8
```

Đo thật ngay khi glm-5.2 còn đang chết:

```
maas_error status=500 model=z-ai/glm-5.2      (lần 1)
maas_error status=500 model=z-ai/glm-5.2      (lần 2)
maas_fallback_used wanted=z-ai/glm-5.2 served_by=gemini/gemini-3.1-flash-lite
→ 13,9s, JSON hợp lệ
```

### 3. Job fail nhưng khách hàng kẹt ở “Đang Deep Research”

`_research()` gọi `fail()` rồi `return` ở 6 nhánh khác nhau, còn `_revert_status()`
chỉ chạy trong `except`. Nên mọi lần fail “có kiểm soát” đều để lại
`customer.status = RESEARCHING` vĩnh viễn — đúng như màn Khách hàng đang hiện. Nay
`run_research()` kiểm tra `job.status == JobStatus.FAILED` sau khi `_research()` trả
về và hạ trạng thái.

---

## 9g-terdecies. Mở rộng nguồn: chính chủ, pháp nhân, LinkedIn, fanpage

Trước đây chỉ có 4 kênh (website, tuyển dụng, LinkedIn, báo chí) và LinkedIn **luôn**
bị đánh `blocked reason=private` — không phải vì bị chặn, mà vì không có truy vấn nào
nhắm tới nó. Đọc log job cũ thấy nguồn thu được phần lớn là bài SEO: giấy phép cầm đồ,
dịch vụ SMS marketing — dùng tên doanh nghiệp làm từ khoá chứ không có dữ kiện.

### 7 kênh nguồn

| Slot | Nhận diện bằng |
|---|---|
| Website chính thức | domain trong hồ sơ, hoặc tên công ty nằm trong host (tự phát hiện) |
| Hồ sơ pháp nhân | masothue.com, hosocongty.vn, thongtindoanhnghiep.co, infodoanhnghiep.com, dangkykinhdoanh.gov.vn, cbonline.ssc.gov.vn, hsx.vn, hnx.vn, mọi `*.gov.vn` |
| LinkedIn công ty | linkedin.com |
| Fanpage & kênh social | facebook.com, fb.com, zalo.me, tiktok.com, youtube.com, instagram.com |
| Trang tuyển dụng | vietnamworks, topcv, itviec, topdev, careerbuilder, jobsgo + từ khoá `tuyen-dung/career/job/viec-lam` trong URL |
| Báo chí ngành | phần còn lại |
| Knowledge Base | tài liệu nội bộ đã chọn |

`ANGLES` thêm `legal` (mã số thuế, người đại diện, vốn điều lệ) và `social` (kênh chính
chủ), nên các vòng sau của agent cũng biết mình còn thiếu hai góc đó.

### Truy vấn mồi: tách `site:` thay vì gộp OR

Mỗi kênh một truy vấn riêng — provider tìm kiếm xử lý toán tử OR không đều nhau, gộp
lại thường mất hẳn nguồn. 15 truy vấn khi có website, 12 khi chưa có. Đo thật trên
VietMoney:

| Truy vấn | Kết quả |
|---|---|
| `site:linkedin.com/company "VietMoney"` | 4 hit |
| `site:facebook.com "VietMoney"` | 4 hit |
| `site:masothue.com "VietMoney"` | 4 hit |
| `site:thongtindoanhnghiep.co "VietMoney"` | 2 hit |
| `site:vietmoney.vn sản phẩm dịch vụ` | 4 hit |
| `"VietMoney" Việt Nam báo cáo thường niên công bố thông tin` | 3 hit |

### Tự tìm ra website chính thức

Hồ sơ `vietmoney` **không có website**, nên mọi truy vấn `site:` bị bỏ và slot “Website
chính thức” luôn `blocked`. Nay `name_key("VietMoney") = "vietmoney"` được so với host:
khớp thì xếp vào slot official, và sau vòng crawl **ghi luôn vào `customer.website`**
— lần sau đã có `site:` để neo, màn Khách hàng cũng hiện đúng domain.

### Gộp mirror host khi dedupe

LinkedIn phục vụ cùng một trang company qua hàng chục subdomain quốc gia. Log run đầu
cho thấy `vn.`, `cn.`, `tw.`, `bo.`, `sv.linkedin.com` chiếm 5 slot cho **một** trang.
`dedupe_key()` chuẩn hoá `www.`/`m.` và gộp mọi subdomain của linkedin/facebook/
youtube/tiktok/cafef về domain gốc.

### Chặn thêm content farm

Thêm vào `research_excluded_domains` những host gặp thật trong log: `congtyluatacc.vn`,
`giaminhcantho.com`, `phanmemcamdo.com`, `zalominiapps.com` (blog giả danh Zalo),
`smsboosting.com`, `cremyx.app`, `cartboss.io`, `sakari.io`.

### Kết quả run thật qua UI (Playwright)

| | Trước | Sau |
|---|---|---|
| Kết cục | `failed` sau **907s** | **`done` sau 68s** |
| Model phục vụ | — | `gemini/gemini-3.1-flash-lite` (fallback, glm-5.2 đang chết) |
| extract | 502 gateway | `attempt=1 finish=stop tokens_in=77.232 tokens_out=898` |
| Nguồn | 63, đa số bài SEO | **60**, đủ 7/7 slot `ok` |
| Dữ kiện | 0 | **7 dữ kiện, 5 cơ hội** |
| Website hồ sơ | trống | `vietmoney.vn` (tự tìm) |

Phân bố 60 nguồn: website chính thức 15 · báo chí 27 · LinkedIn 5 · fanpage/social 6 ·
hồ sơ pháp nhân 5 · tuyển dụng 2.

Dữ kiện rút ra là dữ liệu thật, có số: *“hoạt động từ năm 2016”*, *“hơn 22 cửa hàng,
phục vụ khoảng 150.000 khách hàng”*, *“triển khai Zalo Mini App để định giá tài sản,
tra cứu hợp đồng”*, *“Zalo OA, Facebook Fanpage, Hotline 19008009”*. Profile dựng ra:
`hq: TP. Hồ Chí Minh`, `size: 51-200 nhân viên`, `industry: Dịch vụ tài chính / Cầm đồ`,
`techStack: [Zalo Mini App, AI định giá, ERP, Big Data]`.

Còn nhiễu: truy vấn LinkedIn trả cả `money-forward-vietnam` và `clickaivn` — công ty
khác tên gần giống. Bước extract được yêu cầu bỏ nguồn nói về doanh nghiệp khác, và
7 dữ kiện thu được đều đúng VietMoney, nhưng chúng vẫn tính vào hạn mức nguồn.

---

## 9g-quaterdecies. Hai lỗi lộ ra khi chạy Deep Research cho khách hàng còn lại

### Dữ kiện của các lần chạy trước tích tụ, số liệu đá nhau

Bước dọn trước mỗi lần chạy chỉ xoá dữ kiện có `verdict == AI`. Nhưng dữ kiện đạt
`confidence >= ngưỡng auto-approve (85)` được gán `verdict = OK` **bởi máy**, nên
chúng không bao giờ bị dọn. Kết quả trên `Nhà thuốc Long Châu`: **42 dữ kiện** cho một
khách hàng, trong đó “Quy mô hệ thống” có **3 bản với 3 con số khác nhau** —
2.417 (cuối 2025), 2.639 (6/2026), 2.678 (mới nhất). Người dùng nhìn vào không biết
tin cái nào.

Mốc đúng là `CustomerFact.verified_by_id`: có người xác minh thì giữ, còn lại là dữ
kiện máy sinh và phải bị thay bằng kết quả mới. Đã đổi điều kiện dọn sang
`verified_by_id IS NULL` (bỏ lọc theo verdict), và dọn tay 34 bản tồn của Long Châu →
còn 8 dữ kiện, một con số duy nhất.

### Badge trạng thái ở trang chi tiết khách hàng là chuỗi cứng

`VietMoney` đang `RESEARCHING` trong DB, bảng danh sách hiện đúng “Đang Deep Research”,
nhưng **trang chi tiết** hiện “Hoàn tất nghiên cứu”. Không phải lỗi map dữ liệu: chuỗi
đó nằm cứng trong markup của prototype, không bind vào prop nào:

```
<span style="font:600 10.5px …;color:#12A150;background:#E7F8EF;…">Hoàn tất nghiên cứu</span>
```

Seam `custStatus` thay bằng `{{ custStatus }}` + `{{ custStatusStyle }}`, đọc từ
`cust.status` — **cùng nguồn với bảng danh sách**, và đổi màu theo trạng thái
(đang chạy = hổ phách, hoàn tất = xanh, còn lại = xám).

### Hạn mức đầu vào của extract: siết quá tay rồi nới lại

Khi sửa lỗi 502 tôi hạ `input_share` xuống 0,10 (≈235K ký tự). Nhưng agent thu tới
`max_sources * 1.5 = 120` nguồn, mỗi nguồn tối đa 8K ký tự — nên chỉ ~30 nguồn đầu
tới được model, phần còn lại crawl xong rồi bỏ. Mà đầu vào lớn **không** phải nguyên
nhân 502 (đã đo: prompt 700KB trả về sau 25,8s). Nới lên 0,30 ≈ **704K ký tự**, đủ cho
~88 nguồn. Trần sinh vẫn giữ 16.384 — đó mới là chỗ gây 502.

---

## 9g-quindecies. Bảng khách hàng lệch cột — bỏ cột “Ưu tiên”

Người dùng gửi ảnh: chip trạng thái nằm dưới **NGÀNH**, người phụ trách nằm dưới
**TRẠNG THÁI**, thời gian nằm dưới **PHỤ TRÁCH** — toàn bộ lệch trái một nhịp từ cột
thứ ba.

Nguyên nhân nằm trong chính markup của prototype, hai bảng lệch theo hai kiểu khác
nhau:

| Bảng | Tiêu đề | Cột grid khai | Ô mỗi hàng | Hệ quả |
|---|---|---|---|---|
| Màn Khách hàng | 8 (có “Ưu tiên”) | 8 | **7** — không có ô ưu tiên | lệch trái một nhịp |
| Dashboard | 5 (có “Ưu tiên”) | **4** | 5 | ô cuối rớt xuống dòng |

Bỏ cột thay vì thêm ô, vì **không có dữ liệu thật để đổ vào**: `Customer` trong DB
không có trường độ ưu tiên (chỉ có `source_count`, `fact_count`,
`opportunity_count`, `status`), `_PRIO()` của prototype là High/Medium/Low, còn
`mapCustomers` trong bridge gán cứng `prio: 0`. Thêm ô vào chỉ để mọi khách hàng hiện
“High” — lại là dữ liệu bịa.

Seam `custCols` (7 thay thế): bỏ 2 span tiêu đề “Ưu tiên”, bỏ ô prio chip ở hàng
Dashboard, bỏ 1 ô skeleton, và đổi 3 chỗ khai grid `26px 1.9fr .8fr .9fr 1.3fr .9fr
.9fr 34px` → `26px 1.9fr .9fr 1.3fr .9fr .9fr 34px`. Grid Dashboard giữ nguyên 4 cột
— bỏ ô ưu tiên là còn đúng 4 ô. (Lần đầu tôi cắt luôn một cột grid Dashboard thành 3,
đo lại thấy 4 ô/3 cột nên trả lại.)

Đo trên browser sau khi vá — toạ độ `x` của ô trong hàng trùng khớp tiêu đề:

| | Doanh nghiệp | Ngành | Trạng thái | Cập nhật |
|---|---|---|---|---|
| tiêu đề | 261 | 438 | 527 | 675 |
| hàng Long Châu | 261 | 438 | 527 | 675 |
| hàng VietMoney | 261 | 438 | 527 | 675 |

Màn Khách hàng: 7 cột / 7 ô mỗi hàng, thứ tự đúng DOANH NGHIỆP · NGÀNH · TRẠNG THÁI ·
PHỤ TRÁCH · CẬP NHẬT. Báo cáo vá: **44/44 seam, 0 missed**.

### Kết quả Deep Research hai khách hàng còn lại

| | nguồn | dữ kiện | cơ hội | thời gian | tokens_in |
|---|---|---|---|---|---|
| Nhà thuốc Long Châu | 107 | 30 | 16 | 262s | 190.727 |
| VietMoney (zaloadmin) | 107 | 21 | 11 | 308s | 191.566 |

Cả hai `done`, `attempt=1`, đủ 7/7 slot nguồn, **không còn chủ đề trùng lặp**.
`tokens_in` ~191K so với 77K của lần chạy trước cho thấy hạn mức đầu vào nới lên 0,30
thực sự đưa thêm nguồn tới model — và số dữ kiện tăng theo (Long Châu 8 → 30).

Dữ kiện VietMoney có thứ chỉ hồ sơ pháp nhân mới có: *“Công ty Cổ phần Việt Money
thành lập ngày 18/09/2016”*, *“Digiworld sở hữu 72-73% cổ phần từ cuối 2023”*,
*“theo báo cáo BVSC tháng 2/2025 đang cấu trúc lại”*, *“chuỗi lỗ trung bình 1 tỷ/cửa
hàng theo báo cáo Digiworld 8/2024”*.

---

## 9h. Ba luồng tạo mới trước đây không lưu vào DB

Prototype vốn chỉ ghi vào state cục bộ. Ba chỗ đã nối vào backend:

| Chỗ | Trước | Sau |
|---|---|---|
| `kbCreate` (dialog Knowledge Base) | `state.myKbs` | `POST /knowledge-bases` (+ `/sources` nếu có URL) |
| `saveSkill` (trình soạn skill) | chỉ tăng version rồi toast | `POST /skills` hoặc `PATCH /skills/{id}` |
| `stSave` (lưu proposal thành template) | `this.__tpls` | `POST /templates` |

Một chi tiết dễ nhầm: nút “+ Tạo skill” **không** mở dialog `kbNew` mà điều hướng
sang trình soạn `skilledit`; dialog đó chỉ dành cho Knowledge Base. Vì vậy phần
skill phải vá ở `saveSkill`, không phải ở `kbCreate`.

Các field trong trình soạn là input **uncontrolled** (có giá trị mặc định, không
bind vào state), nên `saveSkill` đọc trực tiếp `.value` từ DOM. Có chốt: chỉ đi
đường backend khi tìm được ≥3 `input` và 1 `textarea`, còn lại giữ hành vi cũ —
tránh gửi dữ liệu rác nếu layout đổi.

Đã kiểm chứng trên browser: gõ tên/mô tả/prompt rồi bấm “Lưu skill” → bản ghi vào
`skills` với đúng nội dung đã gõ, audit `skill.create`.

### `_jsafe` — chốt chặn cho việc vá vào chuỗi JSON

Source component nằm trong một chuỗi JSON, nên **ký tự `"` hoặc newline thô trong
đoạn chèn sẽ cắt chuỗi** và làm vỡ khâu unpack (`Bad control character…`,
`Unexpected non-whitespace character…`). Tôi vướng lỗi này hai lần. Nay mọi đoạn
chèn đi qua `_jsafe()`, raise ngay lúc dựng patch thay vì để trắng màn trên browser.
Trong JS chèn vào thì dùng `“ ”` cho dấu ngoặc kép.

### Soát mock: dùng DB rỗng làm phép thử

DB rỗng là điều kiện tốt nhất để tìm mock — cái gì còn hiện thì cái đó là mock. Đi
hết 7 màn với 20 từ khoá mock đã biết:

| Màn | Kết quả |
|---|---|
| Dashboard, Khách hàng, Proposal, Template Library, Quản trị | sạch |
| Product Knowledge | tĩnh **theo chủ ý** (yêu cầu của bạn) |
| Playbook & Skill | **rỉ mock** → đã sửa |

Chỗ rỉ là `_LOCKED()` (`quocbao` / "Pipeline & giá deal BFSI"). Nó không phải nguồn
dữ liệu riêng mà chỉ là **một entry KB trùng lặp** được `.concat()` vào `_KBS()`, mà
serializer backend đã trả sẵn cờ `locked` cho KB và bridge đã map. Nên đúng cách là
cho nó rỗng khi bridge đã nạp. `_shareReqSeed()` (mock yêu cầu chia sẻ) xử lý cùng
kiểu. Cả hai vào nhóm `EMPTY_SOURCES`.

Kết quả cuối: **0 mock trên mọi màn, 0 crash**, 22/22 seam áp được.

Lưu ý phạm vi: đợt soát này chỉ đi 7 màn danh sách chính. **Màn Thông báo và widget
“Sản phẩm được đề xuất nhiều” trên Dashboard không nằm trong đó** và vẫn còn mock cứng
tới tận §9i — hai chỗ đó không đi qua seam `_XXX()` nào nên phép thử “DB rỗng thì cái
gì còn hiện là mock” không chạm tới.

### Xoá dữ liệu Knowledge Base

Đã xoá 4 KB; cascade dọn `knowledge_files` (3) và `knowledge_sources` (3). Đồng
thời xoá **3 object mồ côi trên MinIO** trong bucket `zbd-knowledge` (5465 byte) —
cascade của Postgres không chạm tới object storage, không dọn tay là rác vĩnh viễn:

```
kb/kb_767cec79cf/fil_13b5494f2a/bang-gia-zns-2026.pdf
kb/kb_767cec79cf/fil_9e38ed93c2/proposal-that.pdf
kb/kb_767cec79cf/fil_b0101bd838/kich-ban-demo.docx
```

Đã dump backup 3 bảng trước khi xoá. Bucket còn 0 object.

### Xoá dữ liệu Playbook & Skill và Template Library

Đã xoá theo yêu cầu (sẽ import lại trên UI). `proposals.template_id` là FK
`NO ACTION` nên phải gỡ tham chiếu trước, còn lại cascade tự dọn:

```sql
UPDATE proposals SET template_id = NULL WHERE template_id IS NOT NULL;  -- 1
DELETE FROM skills;              -- 5  (cascade: skill_versions 11, test_runs 0)
DELETE FROM proposal_templates;  -- 3  (cascade: template_versions 1, slides 26)
```

Đã dump backup `--data-only` của 6 bảng trước khi xoá. `seed.py` không chạy tự
động lúc khởi động (lifespan chỉ import admin từ env), nên dữ liệu **không tự mọc
lại**. Đã kiểm chứng trên UI: hai màn hiện trạng thái rỗng, 0 tên mock.

### Đã kiểm chứng bằng browser thật

- Đăng nhập bằng OTP thật qua UI (mã lấy từ log vì `EMAIL_DRY_RUN=true`).
- Mã lỗi map đúng: `AUTH_DOMAIN`→"Email không thuộc VNG", `AUTH_UNKNOWN`→"chưa có
  trong hệ thống", `OTP_INVALID`→rung ô nhập.
- Dashboard hiện `BHX bachhoaxanh.com`, `LC nhathuoclongchau.com.vn` — row trong DB.
- Skills hiện "Deep Research doanh nghiệp Việt Nam", "Khớp sản phẩm Zalo" (DB);
  tên mock "Customer Research", "Slide Design" **đã mất hẳn**.
- KB hiện "Case study khách hàng Việt Nam", "Bảng giá nội bộ 2026" (chỉ có trong DB).
- Product Knowledge vẫn đủ catalogue gốc, **không** lẫn tên sản phẩm DB.
- Màn Quản trị hiện **9/9 user thật**, 0 email mock (`quocbao@`, `thuha@`,
  `ngoclan@`, `huuphuoc@` đã mất hẳn — chúng chưa từng có trong DB).
- Playbook & Skill và Template Library hiện trạng thái rỗng sau khi xoá DB, **không**
  rơi về mock.
- Đăng nhập bằng mã cố định `123456`, đồng hồ đếm "Mã hết hạn sau 04:58" khớp
  `OTP_TTL_SECONDS=300`.

### Ảnh thiếu: sửa cách phân giải, không phải sửa từng chỗ

Standalone không tham chiếu ảnh bằng đường dẫn mà qua `window.__resources[path]`
— map từ `pptx/media/imageNN.png` sang blob URL, dựng lúc chạy. Ảnh không có trong
map thì biểu thức ra thẳng **chuỗi `"undefined"`**, và CSS thành
`background:url("undefined")` → request tới `/undefined`. Icon nav Dashboard
(`mk('dashboard','Dashboard','55')` → `image55.png`) rơi đúng vào đây.

Sửa ở tầng phân giải: `patch_prototype()` bọc `window.__resources` bằng `Proxy`,
key nào không có mà bắt đầu bằng `pptx/` thì trả `"/" + key` — tức đường dẫn tĩnh
mà backend đã serve từ `src/frontend/`. Một chỗ sửa, đúng cho cả 34 nơi dùng
`__resources`, và **bỏ file ảnh vào là hiện ngay, không cần sửa code**.

Đã kiểm chứng: `url("undefined")` = 0; 5/6 icon nav dùng blob nhúng, Dashboard trỏ
`/pptx/media/image55.png`.

**Vẫn thiếu chính các file ảnh.** Prototype cần 16 file không có trên đĩa —
`image55` (icon Dashboard), `image65`, `image71`, `image72`, `image74`, `image76`,
`image85`, `image89`, `image90`, `image93`, `image100`, `image107`, `image133`,
`image135`, `image136`, `image137`, `image149`. Đây là phần dư của lần import
Design chưa lấy đủ (§9.2). Bỏ file vào `src/frontend/pptx/media/` là xong.

---

## 9i. Thông báo và “Sản phẩm được đề xuất nhiều”

Hai phần này là mock cuối cùng còn sót trên UI. Prototype ghi cứng: `notifTop`
(3 dòng trong chuông), `notifRows` (7 dòng màn Thông báo), `_unread()` đếm cứng
tới 3, bảng “Tóm tắt tuần này” ghi thẳng 6/3/4/2 vào markup, và `topProds` ghi cứng
`ZBS Template Message 68% · Menu & Chatbot 54% · Zalo Mini App 41%`. Không seam nào
giữ lại nhánh mock — hết dữ liệu thì hiện trạng thái rỗng.

### Thông báo: backend đã đủ, chỉ thiếu khâu nối

`/notifications` (list, unread-count, read, read-all, delete, SSE stream) và
`services/notifications.push()` đã có từ đầu, `loadAll()` chỉ chưa gọi tới. Đã thêm
vào bridge cùng `/dashboard`, nâng loadAll từ 9 lên 11 request song song.

Hai thứ backend còn thiếu:

- **`weekSummary` trong `GET /notifications`.** Bảng “Tóm tắt tuần này” cần số đếm
  7 ngày, tính bằng query gộp riêng nên **không bị `limit` của danh sách cắt** —
  lấy 50 dòng mới nhất mà đếm thì tuần nào >50 thông báo là sai.
- **`PATCH /notifications/{id}`** với body `{read: bool}`. Prototype cho bấm ngược
  một dòng về *chưa đọc*, mà API cũ chỉ có `POST /{id}/read` một chiều.

`markAllRead` và `toggleRead` cập nhật lạc quan trước rồi mới gọi API; xong thì gọi
`ZB.reloadNotifs()` — nạp lại đúng một endpoint chứ không phải cả `loadAll()`.

### 9 loại thông báo gộp về 4 nhóm

`NotificationType` có 9 giá trị, màn Thông báo chỉ có 4 chip lọc. Bảng gộp nằm ở
cả hai phía: `WEEK_BUCKETS` trong `api/v1/notifications.py` và `NOTIF_META` trong
`zb-api-bridge.js`.

| Chip | NotificationType |
|---|---|
| Research | `research_done`, `handover` |
| Proposal | `proposal_done`, `approval_request`, `approval_result` |
| Chia sẻ | `share`, `access_request`, `invite` |
| Bình luận | `comment` |

Chip thứ ba của prototype vốn tên **“Sản phẩm”** — đã đổi thành **“Chia sẻ”**, và
dòng “Cập nhật sản phẩm” trong Tóm tắt tuần này đổi thành “Chia sẻ & quyền”. Lý do:
không có `NotificationType` nào phát sinh từ Product Knowledge, giữ nhãn cũ thì chip
đó vĩnh viễn rỗng còn thông báo chia sẻ KB lại đeo chip “Sản phẩm”.

### Icon: chỉ được chọn trong nhóm hiện rõ trên nền trắng

`window.__resources` có 31 ảnh `pptx/media/imageNN.png`, nhưng **10 ảnh trong đó là
icon trắng** dành cho nền tối (17, 18, 27, 33–38, 46, 47) — đặt lên thẻ trắng của màn
Thông báo là mất hút. Đo bằng canvas (đếm pixel không-gần-trắng có alpha đủ cao) thì
nhóm dùng được là 65, 71, 72, 74, 76, 85, 89, 90, 93, 100, 102–105, 107, 108, 133,
135–137, 149.

Mock gốc dính đúng bẫy này: hai dòng dùng `36`/`38` nên chưa bao giờ có icon, còn
dòng “Proposal hoàn tất” trỏ `77` — số không tồn tại trong cả resource map lẫn đĩa.

### `topProducts`: đếm từ `customer_solutions`

Không có bảng nối proposal ↔ product. Nguồn đúng là `customer_solutions` — mỗi dòng
là một giải pháp worker matching đề xuất cho một khách hàng, có `product_id`.

Đếm theo **số khách hàng riêng biệt**, không theo số dòng: một khách có thể có nhiều
giải pháp cùng trỏ về một sản phẩm, đếm dòng thì sản phẩm đó tự phồng lên. Mẫu số là
số khách đã có ít nhất một đề xuất, nên `pct` đọc được là “bao nhiêu phần trăm hồ sơ
đã matching có sản phẩm này”. Truy vấn đi qua `apply_visibility(ResourceType.CUSTOMER)`
cho khớp phần còn lại của `/dashboard`.

Điều hướng khi bấm thanh: khớp `__id` trước, không thấy mới lùi về khớp theo tên
(`_prodBy`) — tên sản phẩm trong DB có thể đổi mà thống kê vẫn phải mở đúng thẻ.

### Đã kiểm chứng bằng browser thật

Playwright MCP bị phiên khác giữ, nên lái Chromium headless qua CDP. Với 3 khách hàng
tạm, 5 `customer_solutions` và 5 thông báo đủ 5 loại:

- Dashboard: `ZNS 100% · Zalo Mini App 33% · Zalo OA 33%` — khớp `customerCount`
  3/1/1 trên mẫu số 3.
- Chuông: badge `5`, ba dòng mới nhất kèm thời gian tương đối thật.
- Màn Thông báo: `5 chưa đọc · 5 thông báo trong 7 ngày qua`, Tóm tắt tuần này
  `2 / 1 / 1 / 1` khớp `weekSummary`.
- 5 chip lọc: Research → 2 dòng, Proposal → 1, Chia sẻ → 1, Bình luận → 1, Tất cả → 5.
- Bấm “Đã đọc” một dòng: `PATCH` 200, unread 5 → 4. “Đánh dấu đã đọc tất cả”:
  `read-all` 200, unread → 0, phụ đề đổi thành `0 chưa đọc`.
- 29 request `/api/v1/*` đều 200, không có lỗi console.

Xoá sạch dữ liệu tạm rồi chạy lại: widget hiện “Chưa có dữ liệu đề xuất…”, chuông hiện
“Chưa có thông báo nào.”, màn Thông báo hiện “Chưa có thông báo nào — Deep Research
xong, proposal sinh xong, bình luận và chia sẻ sẽ hiện ở đây.”, badge tắt.

---

## 9j. Tạo khách hàng + Deep Research: chạy thật thay cho animation

Luồng này là mock cuối cùng còn lại. `createCustomer` chỉ `setState` rồi
`_runResearch()` chạy một chuỗi `setTimeout` với mốc `[6,16,28,…,100]`; nhật ký và
danh sách nguồn là mảng chữ cứng lộ dần theo `progress`; dữ kiện cuối cùng là 3 dòng
viết sẵn về “Thông báo giao dịch / Loyalty / CSKH tự động”. Không có gì chạm DB.

### Gộp hai nút Dashboard

“Chạy Deep Research” và “Tạo khách hàng mới” **vốn gọi cùng một handler**
`openCreate` — hai nút cạnh nhau mở đúng một màn. Đã gộp thành một nút chính
**“Chạy Deep Research khách hàng mới”**, giữ style nút primary có icon.

### Backend đã đủ, chỉ thiếu khâu nối

| Việc | Endpoint |
|---|---|
| Tạo + xếp hàng research | `POST /customers` → `{customer, researchJobId}` |
| Tiến độ + nhật ký | `GET /jobs/{id}` |
| Chạy lại | `POST /customers/{id}/research` |
| Dừng | `POST /research-jobs/{id}/cancel` |
| Dữ kiện + nguồn sau khi xong | `GET /customers/{id}` |
| Xác nhận / loại bỏ dữ kiện | `PATCH /customers/{id}/facts/{factId}` |

Hai chỗ backend phải sửa:

- **`GET /jobs/{id}` mất `message` và thời gian.** `bus.replay()` trả về dataclass
  `Event` chỉ mang `event/data/seq`, trong khi bảng `job_events` có cả `message`
  (nhãn người đọc được) và `created_at`. Nhật ký chạy cần đúng hai trường đó. Đã bổ
  sung vào `Event` và điền trong `replay()` — bản đẩy realtime qua Redis vẫn để trống
  hai trường này vì payload pub/sub không mang theo.
- Lần đầu nối thiếu chính chỗ này nên `/jobs/{id}` trả 500
  (`'Event' object has no attribute 'message'`), UI bắt lỗi rồi hiện panel “thất bại”
  trong khi worker vẫn đang crawl bình thường.

### Bốn bước tiến độ lấy theo stage thật của worker

Prototype có 5 bước cố định (“Sản phẩm & dịch vụ / Quy mô / Thị trường / Đối tượng /
Tổng hợp”) chia đều 0–100. Worker thật đi 4 stage với mốc khác hẳn:
`crawl` 0.10→0.35, `kb` 0.35, `extract` 0.55, `profile` 0.85, xong 1.0. Danh sách bước
đã dựng lại theo đúng bốn mốc đó; bước đang chạy hiển thị `stageLabel` trực tiếp từ
job (ví dụ *“Vòng 1: model đang rà soát 45 nguồn để tìm thêm góc thiếu”*).

### Polling chịu được đứt quãng

`_zbPoll` gọi `GET /jobs/{id}` mỗi 1.5s. Một lần lỗi mạng **không** kết luận thất bại
ngay: đếm tới 4 lần hỏng liên tiếp mới bật panel lỗi, mỗi lần cách nhau 2s. Không có
chốt này thì một nhịp mạng chập là màn hình báo “Deep Research thất bại” trong khi
worker vẫn chạy — đã gặp đúng trường hợp đó khi server test bị kill giữa chừng.

Panel lỗi cũng đã bỏ chữ cứng: `rFailBody` lấy `error` của job, `rFailReasons` dựng từ
stage đang dừng + số nguồn lấy được + ba nguồn hỏng đầu tiên.

### “Chạy lại Research” bám vào job đang chạy

`rerunResearch` bản gốc chỉ đặt `createdName`/`createdWeb`, không mang `__id`, nên
`_runResearch` mới không biết chạy cho khách nào. Đã thêm `createdId: cust.__id`.
Backend trả lại job đang chạy nếu có (`_active_job`), nên bấm nút này lúc job chưa xong
= **bám vào job đó** chứ không đẻ thêm job mới.

### Đã kiểm chứng bằng browser thật

Tạo `ZBCHK Nhà thuốc Long Châu` + `nhathuoclongchau.com.vn` qua đúng form trên UI:

- `POST /customers` 200 → khách hàng có thật trong DB, bridge nạp lại thấy ngay.
- Worker chạy thật: 79 `customer_sources`, 41 `customer_facts` sau khi xong.
- Màn research hiện tiến độ 10% → 25% → … theo job, 29 lần poll `/jobs/{id}` đều 200.
- Nhật ký hoạt động là URL thật đã crawl kèm giờ thật: `nhathuoclongchau.com.vn: ok`,
  `vieclam.nhathuoclongchau.com.vn: ok`, `e-magazine.asiamedia.vn: ok`, …
- Bấm “Dừng research” → `POST /research-jobs/{id}/cancel` → DB `status=cancelled`,
  `cancel_requested=t`, dừng ở đúng 0.25.

### Hồ sơ khách hàng cũng là output của Deep Research

`_CRM()` là seam cuối cùng còn mock — cả report “Hồ sơ” lẫn tab “Deep Research” của màn
khách hàng đều dựng bằng chữ cứng về The Coffee House. Đúng bản chất thì đây là **kết
quả Deep Research đã lưu**, không phải nội dung riêng.

Nguồn: `GET /customers/{id}` trả `profile` (worker ghi ở stage `profile`), `facts`,
`sources`, `activity`. Bridge thêm `ZB.customerDetail(id)`; component memo hoá theo
`__id` trong `_zbCustDetail()` nên mở lại hồ sơ không gọi API lần nữa.

`_CRM()` giờ dựng 5 mục từ dữ liệu thật, thay cho 6 mục viết sẵn:

| Mục | Nguồn |
|---|---|
| Tổng quan doanh nghiệp | `profile.marketPosition` + industry / market / size / hq |
| Kênh số đang dùng | `profile.channels` |
| Công nghệ đang dùng | `profile.techStack` |
| Nhu cầu & cơ hội Zalo | `facts` có `isOpportunity` |
| Dữ kiện đã thu thập | `facts` còn lại |

`crmTldr` (“Tóm tắt 30 giây”) lấy 5 dữ kiện có `confidence` cao nhất. Các chỗ chữ cứng
còn lại trong tab đã nối hết: ghi chú (`notes`), dòng cập nhật (`researchedAt` + số
nguồn), số run (`activity` lọc `research_started`), người phụ trách (`owner`), thị
trường (`profile.market`), và hai dòng đếm ở cột phải.

Nút Giữ / Xác nhận / Loại bỏ trên từng dữ kiện gọi thẳng
`PATCH /customers/{id}/facts/{factId}`, cập nhật lạc quan trước rồi đồng bộ.

### Bố cục tab Hồ sơ: dựng lại vì dữ liệu thật dài gấp bội mock

Nối xong dữ liệu thật thì bố cục cũ vỡ: nó vốn thiết kế cho 6 mục × 3 dữ kiện viết
sẵn, còn research thật trả 54 dữ kiện + 7 kênh + 8 công nghệ. Mỗi mục — kể cả loại
một dòng như “Zalo OA” hay “CRM” — đều thành một thẻ full-width kèm pill nguồn lặp
lại. **Trang cao 35.749px trên viewport 896px: 40 màn cuộn.**

Bốn lỗi bố cục và cách sửa:

| Vấn đề | Sửa |
|---|---|
| Tab lặp lại nguyên khối nhận diện (tên 27px, website, chip) trong khi header ngay trên đã có | Bỏ khối trùng, chỉ còn eyebrow + tóm tắt |
| `hq`/`size`/`market`/`industry` nhét vào chip bo tròn, bị cắt `…` giữa chừng | Lưới key–value tự co `auto-fit minmax(220px,1fr)` |
| Kênh & công nghệ: 15 thẻ full-width | 2 nhóm chip, xuống dòng tự do |
| 54 dữ kiện trộn chung, không phân biệt cơ hội với dữ kiện nền | Tách “Cơ hội Zalo” (lưới thẻ 2 cột) khỏi “Dữ kiện đã thu thập” (dòng gọn, clamp 2 dòng) |

Mỗi thẻ/dòng giờ có đủ ba thứ BD cần: độ tin cậy, nguồn, và nút duyệt — nút chỉ hiện
khi `verdict === 'ai'`. Cột trái thêm badge đếm cho từng mục lục.

Kết quả: **35.749px → 5.795px** (−84%), cùng lượng dữ liệu.

### Modal “Report đầy đủ”: pill nguồn `flex:none` bóp chết cột chữ

Bố cục cũ đặt chữ và pill nguồn cùng một hàng flex, pill để `flex:none`. Mock có
nguồn ngắn kiểu *“Website · Báo chí”* nên không lộ; dữ liệu thật là URL 100+ ký tự,
pill giãn hết ~1000px và ép cột chữ (`flex:1;min-width:0`) còn ~120px — **mỗi dòng
một chữ**. Đã tách nguồn xuống dòng riêng bên dưới, `text-overflow:ellipsis`, và ẩn
hẳn khi dữ kiện không có URL (các dòng suy ra từ `profile`).

### Nguồn hiển thị bằng hostname, không phải URL đầy đủ

`_zbHost()` cắt còn tên miền: `chungta.vn` thay cho
`https://chungta.vn/photo/nguoi-fpt/nguoi-dep-long-chau-chin-chu-luc-lam-viec-…`.
Với BD thì tên miền mới là tín hiệu (báo chí / website khách / wiki), phần path chỉ
là nhiễu và là thứ làm vỡ mọi bố cục có URL. Áp cho cả 6 chỗ hiện nguồn: thẻ cơ hội,
dòng dữ kiện, danh sách nguồn ở tab Deep Research, và modal report.

Phụ đề modal (`Run #4 · 14 nguồn · 42 dữ kiện`) cũng đã nối vào số thật.

Kiểm chứng trên hồ sơ `Nhà thuốc Long Châu` sau 4 run thật: chip hiện “Bán lẻ dược phẩm
& Chăm sóc sức khỏe · Việt Nam · 10.000-19.999 nhân viên · 379-381 Hai Bà Trưng…”, mục
lục đúng 5 mục ở trên, “DEEP RESEARCH RUN #4” khớp 4 dòng `research_started` trong DB,
“73 nguồn” khớp `customer_sources`.

---

## 9k. Audit Log

Bảng `audit_logs` và `GET /admin/audit` có từ đầu, `audit.record()` được gọi ở 50+ chỗ
— nhưng màn Quản trị vẫn đọc mảng `auditRows` viết sẵn 8 dòng về The Coffee House.

### Bộ lọc cũ không khớp enum backend

Chip lọc của prototype: `Tất cả · Đăng nhập · Dữ liệu KH · Sản phẩm · Proposal ·
Phân quyền`. `AuditCategory` thật: `Dữ liệu KH · Proposal · Phân quyền · Tri thức ·
Hệ thống`. Hai chip **Đăng nhập** và **Sản phẩm** không tồn tại phía DB (sẽ luôn rỗng),
còn **Tri thức** và **Hệ thống** thì không có chip để lọc. Nay chip dựng từ trường
`categories` mà chính endpoint trả về, nên không thể lệch nữa.

### `action` là khoá máy, cần bảng nhãn

DB lưu `customer.create`, `proposal.regenerate`, `access_request.approve`… Bridge có
`AUDIT_ACTIONS` map 50 khoá sang tiếng Việt (`tạo khách hàng`, `sinh lại proposal`,
`duyệt yêu cầu truy cập`). Khoá lạ thì hiện nguyên bản chứ không bịa.

`user_ref` không trả màu avatar, nên màu suy ra từ hash handle — thuần trình bày, ổn
định giữa các lần render.

### Modal “Chi tiết” bịa IP và thiết bị

Bản cũ sinh `10.24.8.<số>`, `Chrome 138 · macOS`, `AL-2026-07-26-1000` từ chỉ số dòng.
`GET /admin/audit/{id}` có sẵn `ip`, `userAgent`, `meta` thật — đã nối, và đổ thêm mọi
khoá trong `meta` xuống cuối bảng.

### Bug backend lộ ra: `from`/`to` chưa bao giờ chạy

Hai tham số lọc thời gian khai báo `str | None` rồi so thẳng với cột
`DateTime(timezone=True)`:

```
asyncpg.exceptions.UndefinedFunctionError:
operator does not exist: timestamp with time zone >= character varying
```

Nghĩa là endpoint luôn 500 khi có `from`/`to` — không ai gọi nên chưa lộ. Đổi kiểu
sang `datetime | None` để FastAPI tự parse ISO-8601; sai định dạng giờ trả 422 thay vì
500.

### Đã kiểm chứng

- 243 bản ghi thật, chip lọc `Dữ liệu KH` → “Tải thêm 7 bản ghi” (27 dòng), `Tất cả` →
  “Tải thêm 223” — khớp `total` của API.
- Modal chi tiết: `IP 127.0.0.1`, `curl/8.7.1`, `aud_f654fad767` — đọc từ DB.
- Dropdown “7 ngày qua” giờ gửi `from` thật; `size` tăng dần theo nút Tải thêm.

Ghi chú: `_patch_pdf_only` của đợt trước có một anchor nằm trong chính mảng mock audit
(`d.action || 'xuất PPTX proposal'`). Mock đi thì anchor đi theo — đã gỡ entry đó và hạ
số hit kỳ vọng 22 → 21.

---

## 9c. Quản lý user và quyền admin

**Nguồn sự thật là bảng `users` trong DB** — không hardcode email trong code.

```bash
uv run python -m app.users grant-admin  <email> [...]   # tạo nếu chưa có + set ADMIN
uv run python -m app.users revoke-admin <email>         # hạ về MEMBER
uv run python -m app.users list                         # xem role thật trong DB
```

Admin hiện tại (đã ghi vào DB): `khanhpp@vng.com.vn`, `lanlt2@vng.com.vn`,
`huonglx@vng.com.vn`.

### Admin khởi tạo đặt ở env, import vào DB khi run

```
BOOTSTRAP_ADMIN_EMAILS=khanhpp@vng.com.vn,lanlt2@vng.com.vn,huonglx@vng.com.vn
```

Chạy ở **hai chỗ**, nên `uvicorn` hay `python -m app.seed` đều ra cùng kết quả:

- `app/main.py` lifespan — mỗi lần app khởi động
- `app/seed.py` — sau khi tạo user demo

Hành vi (`app/users.py::ensure_bootstrap_admins`):

| Tình huống | Kết quả |
|---|---|
| Email chưa có trong DB | Tạo user mới, role ADMIN, status active |
| Đã có nhưng là MEMBER | Nâng lên ADMIN |
| Đã có và đã là ADMIN | Không làm gì (idempotent) |
| Đang `locked` | Mở lại thành active |
| Email ngoài `ALLOWED_EMAIL_DOMAINS` | Từ chối + log `bootstrap_admin_rejected`, không tạo rác |
| Xoá email khỏi biến env | **Không hạ quyền ai** |

Đã kiểm chứng: hạ `lanlt2` và `huonglx` về MEMBER rồi restart → log
`bootstrap_admins changed=['lanlt2@… (updated: role=ADMIN)', 'huonglx@… (updated:
role=ADMIN)']`, DB về lại ADMIN. Khởi động lần 2 báo "không có thay đổi".
`hacker@gmail.com` bị chặn vì sai domain.

Sau khi mồi xong, quản lý bằng `/admin/users` hoặc các lệnh `app.users` ở trên.
`app/seed.py` chỉ tạo user demo nếu chưa có và **không bao giờ đổi role** của
user đã tồn tại — chỉ `ensure_bootstrap_admins` mới nâng quyền.

---

## 9c-bis. Tên người dùng chỉ suy ra từ email

Hệ thống **không có nguồn nào** cho tên thật, nên mọi tên đầy đủ kiểu “Phạm Phú
Khánh” trong DB đều là dữ liệu bịa. Nay `User.name` luôn là **phần trước @** của
email (`khanhpp@vng.com.vn` → `khanhpp`), `initials` lấy 2 ký tự đầu của handle.

Đã bỏ đường nhập tên ở mọi chỗ tạo user: `users.py`, `provisioning.py`, `seed.py`
đều dùng `handle_of(email)`; field `name` bị xoá khỏi `InviteCreate` và
`AdminUserPatch` (đó là admin tự đặt tên hộ người khác — chính thứ cần loại bỏ).
`ProfilePatch.name` giữ lại vì đó là người dùng tự khai, không phải hệ thống bịa.

Lệnh dọn dữ liệu cũ, mặc định dry-run:

```
python -m app.users sync-names           # liệt kê chỗ lệch
python -m app.users sync-names --apply
```

Nó cũng viết lại **audit log**: bảng `audit_logs` lưu tên hiển thị vào `actor_label`
và mở đầu `summary` bằng chính nó, nên đổi được chính xác mà không phải đoán chuỗi
cũ. Đã đổi 7 user + 111 dòng audit; 34 dòng còn tên người khác nằm giữa câu
(“khanhpp bàn giao Napas cho …”) và trong `search_norm` được dọn bằng một lượt map
một lần — `_relabel_audit` nay thay **mọi vị trí** chứ không chỉ tiền tố.

## 9c-ter. Biết user vào webapp lần đầu để chạy onboarding

**`is_first_login` không dùng được cho việc này.** Cờ đó bị xoá ngay trong
`issue_session()` — tức lúc cấp token, **trước khi** người dùng xem xong onboarding.
Đóng tab giữa luồng là mất vĩnh viễn, không bao giờ thấy lại.

Đúng tín hiệu phải là *đã hoàn tất chưa*, nên thêm hai property trên `User`:

```python
ONBOARDING_STEPS = ("profile", "team", "interests")

@property
def needs_onboarding(self) -> bool:
    return bool(set(self.ONBOARDING_STEPS) - set(self.onboarding_steps or ()))

@property
def needs_tour(self) -> bool:
    return not self.tour_completed
```

`needsOnboarding` / `needsTour` được trả ở **`/me`**, **`/me/onboarding`** và trong
response đăng nhập (kèm `requiredSteps` để frontend không phải đoán danh sách bước).
Prototype nay gate theo `needsOnboarding` thay vì `isFirstLogin`.

Nửa còn lại: prototype trước đây hoàn tất onboarding chỉ set state cục bộ
`onboarded: true`, **không ghi DB** — lần sau đăng nhập lại hiện tiếp. Nay 3 seam
(`obNext` bước 3, `finishOnboarding`, và `tourDone` khi bấm Escape) gọi
`ZB.completeOnboarding()` / `ZB.completeTour()` → `PATCH /me/onboarding`.

Chênh lệch giữa hai cách nhìn thấy rõ khi đăng nhập 4 user:

| user | isFirstLogin | needsOnboarding |
|---|---|---|
| khanhpp | false | false |
| lanlt2 | **false** | **true** |
| huonglx | true | true |
| zaloadmin | **false** | **true** |

`lanlt2` và `zaloadmin` đã bị xoá cờ `is_first_login` nhưng **chưa từng onboarding**
— logic cũ sẽ bỏ qua họ mãi mãi.

Đã kiểm chứng trọn vòng trên browser: `lanlt2` → thấy Bước 1/3 → đi hết 3 bước →
`completedSteps: [profile, team, interests]`, `needsOnboarding: false`; Escape đóng
tour → `tourCompleted: true`; đăng nhập lại → vào thẳng Dashboard “Chào lanlt2 👋”.

### 4 tài khoản khởi tạo

```
BOOTSTRAP_ADMIN_EMAILS=khanhpp@vng.com.vn,lanlt2@vng.com.vn,huonglx@vng.com.vn,zaloadmin@vng.com.vn
OTP_BYPASS_EMAILS=zaloadmin@vng.com.vn
```

`zaloadmin` trước đây phải tạo tay bằng `grant-admin`; nay nằm trong danh sách
bootstrap nên DB mới khởi tạo là có đúng 4 user. Đã xoá 5 user demo của `seed.py`
(`anhtuan`, `minhthu`, `quochuy`, `thanhha`, `newbd`).

---

## 9c-quater. Product Knowledge: crawl một lần, lưu DB, matching chỉ đọc

**Product Matching không bao giờ crawl.** `_match()` chỉ `select(Product)` từ DB —
không gọi mạng, không search. Crawl là hành động riêng và tường minh
(`POST /products/{id}/crawl`, nút “Cập nhật dữ liệu nguồn”). Đã kiểm chứng bằng cách
đếm log `product_crawl_source` trước/sau khi chạy matching: **không tăng**.

### Crawl trước đây chỉ search, không fetch

`_crawl()` đưa `source_url` vào **search engine** thay vì tải trang. Với URL đã biết
thì đó là đường vòng, và bảng giá hầu như luôn ở trang con nên search không lấy được
— kết quả là `pricing` rỗng cho mọi sản phẩm.

Nay `fetch_pages()` tải trực tiếp bằng httpx, rồi **tự lần sang trang bảng giá cùng
host** (sâu 1 cấp, khớp `pricing|bang-gia|gia-ca|price`), tối đa 3 trang × 24k ký tự.
Search chỉ còn là fallback khi fetch thất bại; `result` của job ghi rõ
`mode: fetch|search` và danh sách URL đã đọc. HTML → text bằng regex strip (không
thêm dependency parser).

Khác biệt đo được trên `zalo.solutions/oa`: search → 0 dòng giá; fetch → tự tìm
`/oa/pricing`, ra **10 dòng giá đúng bản chính thức**.

### Hai bug lộ ra khi làm

**Job báo `failed` dù dữ liệu đã lưu.** `finish()` vẫn tham chiếu `outcome` — biến
chỉ tồn tại trong nhánh search. Fetch thành công thì `outcome` không tồn tại →
`UnboundLocalError` sau khi đã commit. Nay dùng `len(pages)` và trả metadata đầy đủ.

**JSON bị cắt giữa dòng.** Profile `product.crawl` chỉ có `max_tokens=4096`, không đủ
cho 7 field kèm bảng giá nhiều dòng → `Model trả về nội dung không phải JSON hợp lệ`.
Nâng lên 12.288 tokens, timeout 300s.

### Chặn model bịa giá

Prompt matching giờ nêu rõ: `pricingNote` **chỉ ghi khi danh mục có mục Giá**; không
có thì để rỗng, vì ghi chú này đi thẳng vào proposal gửi khách. Cũng buộc `package`
phải nằm trong danh sách gói của chính sản phẩm đó.

Đồng thời prompt trước đây **bỏ rơi dữ liệu đang có**: `positioning` và
`targetIndustries` không hề được gửi cho model, dù đó là hai tín hiệu hữu ích nhất
để khớp theo ngành. Nay khối danh mục gồm đủ Mô tả / Định vị / Năng lực / Ngành phù
hợp / Gói / Use case / Giá — từ 1.660 lên 2.927 ký tự.

### Trạng thái dữ liệu (tính tới 29/07/2026)

| Sản phẩm | Nguồn | Giá | Use case | Đủ cho proposal? |
|---|---|---|---|---|
| ZNS – Business Message | `zalo.solutions/business-message` + `/pricing` | **10 dòng** | 6 | có |
| Zalo OA | `zalo.solutions/oa` + `/oa/pricing` | **10 dòng** | 4 | có |
| Zalo Mini App | `miniapp.zaloplatforms.com` | 0 | 3 | định tính, **không có giá** |
| Zalo Ads | *chưa có* | 0 | 0 | **không** — dữ liệu seed |
| ZaloPay Gateway | *chưa có* | 0 | 0 | **không** — dữ liệu seed |
| Zalo Cloud Account | *chưa có* | 0 | 0 | **không** — dữ liệu seed |

`mini.zalo.me` **redirect 301 về `miniapp.zaloplatforms.com`**, tức đó là trang
canonical và giá Mini App không được công bố ở đó. `zalo.ai` redirect sang
`ai.zalo.solutions` — trang render bằng JS, fetch ra vỏ rỗng. Homepage
`zalo.solutions` liệt kê 4 sản phẩm (OA, Business Message, Mini App, Zalo AI) —
tức **Zalo AI đang thiếu trong DB**, còn Zalo Ads / ZaloPay / Zalo Cloud Account
**không nằm trong danh mục của nguồn đó**.

**Rủi ro đã quan sát được**: chạy matching thử, `ZaloPay Gateway` được đề xuất ở
`fitScore 72` dù năng lực của nó là chữ seed tự viết. Quy tắc chặn giá giúp
`pricingNote` rỗng, nhưng phần năng lực/định vị vẫn được trình bày như sự thật.
Cách chặn nhanh mà không mất dữ liệu: đổi `status` của 3 sản phẩm đó khác `active`
— `_match()` đã lọc `status == "active"` nên chúng biến khỏi matching ngay, và bật
lại được bất cứ lúc nào.

---

## 9d. Harness layer cho MaaS — tuning theo từng tác vụ

Trước đây `MAAS_MAX_TOKENS` / `MAAS_TEMPERATURE` / `MAAS_TOP_P` /
`MAAS_TIMEOUT_SECONDS` là **giá trị toàn cục trong env**, dùng chung cho mọi lời
gọi. Sai về mặt hiệu năng: rút dữ kiện cần đầu ra dài và nhiệt độ thấp, còn chat
trong editor cần nhiệt độ cao và phản hồi nhanh; OCR ảnh cần timeout dài hơn
nhiều nhưng token thì ít.

`app/integrations/harness.py` giữ **profile riêng cho từng tác vụ**:

| Profile | Model | max_tokens | temp | top_p | timeout | JSON |
|---|---|---:|---:|---:|---:|:--:|
| `research.plan` | glm-5.2 | 4.096 | 0.35 | 0.90 | 180s | ✓ |
| `research.extract` | glm-5.2 | 65.536 | 0.20 | 0.90 | 1200s | ✓ |
| `matching.solutions` | glm-5.2 | 8.192 | 0.35 | 0.90 | 300s | ✓ |
| `proposal.outline` | glm-5.2 | 8.192 | 0.40 | 0.92 | 300s | ✓ |
| `proposal.slide` | glm-5.2 | 6.144 | 0.50 | 0.95 | 180s | ✓ |
| `editor.chat` | glm-5.2 | 8.192 | 0.60 | 0.95 | 180s | |
| `skill.test` | glm-5.2 | 8.192 | 0.40 | 0.95 | 300s | |
| `product.crawl` | glm-5.2 | 4.096 | 0.30 | 0.90 | 180s | ✓ |
| `kb.summary` | glm-5.2 | 768 | 0.30 | 0.90 | 120s | |
| `vision.ocr` | gemma-4-31b-it | 8.192 | 0.10 | 0.90 | 600s | |
| `vision.describe` | gemma-4-31b-it | 2.048 | 0.20 | 0.90 | 180s | |

### Context window thật của model

| | Context | Output cap |
|---|---:|---:|
| `z-ai/glm-5.2` | 1.000.000 token | 131.072 token |
| `google/gemma-4-31b-it` | 256.000 token | 8.192 (đặt thận trọng) |

Harness clamp `max_tokens` theo `output_cap` nên không thể đặt vượt giới hạn thật.
Mỗi profile còn có `input_share` để tính `input_budget_chars` — ngân sách ký tự
cho phần prompt, dùng khi dựng corpus:

- `research.extract`: `input_share=0.80` → **~1,75 triệu ký tự** corpus. Đây là
  chỗ tận dụng cửa sổ 1M: nạp gần như toàn bộ nguồn thu được thay vì cắt ở 60k
  ký tự như trước.
- `vision.ocr`: 256k context → ~353k ký tự.

Lý do chọn số: `research.extract` nhiệt độ thấp nhất vì bịa số liệu là lỗi nặng
nhất, và token cao nhất vì phải trả về hàng chục dữ kiện. `vision.ocr` nhiệt độ
0.1 vì OCR cần gần như tất định, timeout 300s cho PDF nhiều trang.
`kb.summary` chỉ 512 token vì đầu ra là 2–3 câu.

Client dùng profile thay vì rải tham số:

```python
await maas.run("research.extract", EXTRACT_SYSTEM, prompt, admin=cfg)
await maas.look("vision.ocr", prompt, [pdf_bytes], mime_type="application/pdf")
async for chunk in maas.run_stream("editor.chat", messages): ...
```

Timeout được truyền **theo từng request** (`httpx.Timeout` ở mức call), không
nướng cứng vào `AsyncClient` dùng chung — nếu không thì mọi tác vụ lại chia sẻ
một hạn mức.

### Thứ tự ưu tiên

1. Tham số ở call site (ví dụ Skill có `model`/`temperature` riêng của nó)
2. `AiConfig` trong DB (màn Quản trị §12) — chỉ đè **model** và làm **trần**
   `max_tokens`
3. Profile trong harness

Điểm 2 cố ý **không** đè `temperature`: một ô nhiệt độ toàn cục ở màn admin sẽ
xoá sạch toàn bộ tuning theo tác vụ (đã gặp — mọi profile reasoning về hết 0.5).
Nhiệt độ thuộc về harness; admin chỉ chọn model và giới hạn chi phí token.

Xem tham số đang thực sự áp: `GET /api/v1/admin/ai-profiles` (trả cả
`defaults` và `effective` sau khi áp `AiConfig`).

Các biến `MAAS_MAX_TOKENS` / `MAAS_TEMPERATURE` / `MAAS_TOP_P` /
`MAAS_TIMEOUT_SECONDS` trong env giờ chỉ còn là fallback cho lời gọi ad-hoc
không đi qua profile. Env chỉ nên giữ `AI_PLATFORM_API_KEY`, `MAAS_BASE_URL`,
`MAAS_REASONING_MODEL`, `MAAS_VISION_MODEL`.

---

## 9e. Tài khoản vào thẳng không cần OTP

`zaloadmin@vng.com.vn` đăng nhập không cần OTP: `POST /auth/otp/request` thấy
email nằm trong danh sách bypass thì cấp token luôn và trả `{"direct": true}`;
bridge nhận cờ đó và cho vào dashboard, không hiện màn OTP.

Cấu hình bằng **env**, không hardcode:

```
OTP_BYPASS_EMAILS=zaloadmin@vng.com.vn
```

Tài khoản vẫn là user bình thường trong bảng `users` (tạo bằng
`python -m app.users grant-admin zaloadmin@vng.com.vn`), nên vẫn chịu mọi phân
quyền và vẫn bị chặn nếu `status = locked`.

**Ba chốt an toàn** — vì đây là đường bỏ qua xác thực:

1. **Tự tắt khi `APP_ENV=production`.** Muốn bật thật phải đặt thêm
   `ALLOW_OTP_BYPASS_IN_PRODUCTION=true`. Đã kiểm chứng: với `APP_ENV=production`,
   `is_otp_bypass()` trả `False`.
2. **Ghi audit mỗi lần dùng** — `auth.bypass_login`, danh mục "Phân quyền",
   thấy được ở màn Audit Log.
3. **Log mức `warning`** (`otp_bypass_login`) kèm email, env và IP.

Email không nằm trong danh sách vẫn đi luồng OTP bình thường — xem 9f.

---

## 9f. Kênh OTP: mã cố định cho dev, email cho thật

Mã OTP hết hạn sau **5 phút** (`OTP_TTL_SECONDS=300`). Trước đây đặt 90 giây,
lệch với chữ trên màn hình prototype ("hiệu lực 5 phút") — nay đã khớp. Email
template lấy số phút từ chính biến này (`round(ttl/60)`) nên không lệch lại được.

### Chế độ đang bật: mã cố định, không gửi email

```
OTP_DEV_CODE=123456
ALLOW_OTP_DEV_CODE_IN_PRODUCTION=false
```

`request_otp()` thấy `otp_dev_code_active` thì dùng luôn mã đó, **bỏ hẳn bước gọi
Resend**, và trả thêm `{"devCode": "123456", "channel": "dev"}` để UI/người test
biết mà gõ. Mã vẫn đi qua đúng đường cũ — vẫn hash HMAC, vẫn lưu `otp_challenges`,
vẫn hết hạn 5 phút, vẫn đếm số lần sai và khoá sau 5 lần. Nên đây là "thay nguồn
sinh mã", không phải "tắt xác thực": gõ sai vẫn bị từ chối.

Đã kiểm chứng: `123456` → 200 kèm token (role ADMIN); `999999` → 400
`OTP_INVALID` kèm `attemptsLeft: 4`.

Hai chốt an toàn: **tự tắt khi `APP_ENV=production`** (kiểm chứng:
`otp_dev_code_active=False`), và log mức `warning` (`otp_dev_code`) mỗi lần cấp.
Muốn quay lại OTP ngẫu nhiên gửi qua email: để `OTP_DEV_CODE=` rỗng.

Thứ tự ưu tiên khi cùng bật: **bypass (9e) trước** — `zaloadmin@vng.com.vn` vào
thẳng, không sinh mã. Các email khác mới đi nhánh mã cố định.

### Vì sao email OTP trước đó không tới

Ba nguyên nhân độc lập, đã tìm ra bằng cách gọi thẳng API Resend:

1. **`EMAIL_DRY_RUN=true`** — mọi email chỉ ghi log `email_dry_run`, không gửi.
2. **Tài khoản Resend chưa verify domain nào** (`GET /domains` → `data: []`), và
   sender là `onboarding@resend.dev` — sandbox dùng chung. Resend **tự trả 403**
   với mọi người nhận khác chủ tài khoản:
   *"You can only send testing emails to your own email address
   (zbdagentflow@gmail.com)."* Đây là chặn ở phía Resend, code không lách được.
   Gửi về đúng `zbdagentflow@gmail.com` thì thành công (id `2ccb858a…`) — tức key
   và code đều đúng.
3. **Mọi link trong email là đường dẫn tương đối** (`/?invite=…`,
   `/proposals/{id}`). Trong email client đó là link chết — nút "Kích hoạt tài
   khoản" bấm không ra gì.

### Hai thứ đã sửa để lúc bật email là chạy được

**`APP_BASE_URL`** + `_abs()` trong `resend_client`: mọi `send_*` tuyệt đối hoá
URL trước khi dựng cả bản HTML và bản text. Sửa một chỗ, đúng cho cả 8 loại email
— không phải đi vá 10 call site.

**`EMAIL_SANDBOX_TO`** — cách dùng tiếp sender chung mà vẫn gửi thật: mail dồn về
hộp chủ tài khoản, tiêu đề thành `[→ nguoinhan@that.vn] …` và thân mail có banner
vàng ghi người nhận gốc. Nhờ đó test được luồng invite/OTP thật khi chưa có domain.
Để rỗng thì tắt.

Muốn gửi thật tới `@vng.com.vn` thì **buộc phải verify domain** ở
resend.com/domains rồi đổi `RESEND_FROM_EMAIL` sang domain đó. Gói free vẫn cho
verify 1 domain — vướng ở quyền DNS, không phải ở tiền.

### Tên thương hiệu: một nguồn duy nhất

Trước đây tên sản phẩm bị viết cứng ở 10 chỗ trong `resend_client.py` với **ba biến
thể lệch nhau** — “ZB Business Development”, “ZB Business Development Agent”,
“ZB Agent”, cộng thêm tiêu đề OTP ghi “mã đăng nhập ZB”. Đổi tên tab thành
`zBD Agent` làm email lệch hẳn với UI.

Nay mọi template email dùng **`settings.app_name`**, không còn chữ cứng. Đổi
`APP_NAME` trong `.env` là toàn bộ email, `/api`, và title OpenAPI đổi theo — không
phải đi sửa 10 chỗ nữa.

```
APP_NAME=zBD Agent
RESEND_FROM_EMAIL=zBD Agent <onboarding@resend.dev>
```

Đã kiểm chứng Resend nhận **tên hiển thị** kèm sender sandbox dùng chung
(`zBD Agent <onboarding@resend.dev>` → 200), nên hộp thư người nhận thấy “zBD Agent”
chứ không phải địa chỉ trơ. Gửi thật OTP + email mời: tiêu đề ra
`123456 là mã đăng nhập zBD Agent của bạn` và
`Phạm Phú Khánh mời bạn dùng zBD Agent`.

### Email mời thành viên

`invite_user()` gọi `resend.send_invite()` sau khi tạo `Invite` (token, hạn 7
ngày). Trước đây bọc `except Exception: pass` — lỗi gửi mail biến mất không dấu
vết, đúng kiểu bug khó tìm. Nay ghi `invite_email` (kèm `delivered`, `dry_run`,
`redirected_to`) khi xong và `invite_email_failed` khi lỗi. Vẫn **không** rollback
việc tạo user: mời được người vào hệ thống quan trọng hơn việc email có tới hay
không, và người đó vẫn nhận notification trong app.

### Favicon

Prototype gốc không có thẻ `<link rel="icon">` nào, nên kể cả đặt file vào cũng
không hiện. Nay `patch_prototype()` chèn `<link rel="icon" href="/favicon.ico">`
(seam thứ 12, `iconTag`), và route `/favicon.ico` tự dò file đầu tiên tìm được
trong `assets/favicon.{ico,svg,png}` → `favicon.{ico,svg,png}`, trả đúng
`Content-Type` theo đuôi. Chưa có file thì trả **204** thay vì 404 để không bẩn log.
Bỏ file vào `src/frontend/assets/` là hiện, không cần sửa code.

---

## 10. Ghi chú vận hành

- **MaaS/search thiếu key** không làm app chết: `/health` báo `degraded`, và job
  liên quan trả mã lỗi đúng để UI hiện màn tương ứng. Chỉ db/redis/storage chết
  mới trả 503 và bật banner đỏ.
- **`max_tries = 1`** ở worker: job idempotent theo **bản ghi**, không theo lần
  chạy, nên không tự retry — chạy lại là hành động tường minh của người dùng
  (`/research`, `/regenerate`, `/exports/{id}/retry`).
- **`job_timeout = 1800`**: một lượt deep research qua 14 nguồn kèm nhiều lượt
  gọi LLM là chậm.
- **`maxmemory-policy noeviction`** cho Redis: hàng đợi job và pub/sub không được
  phép mất message.
