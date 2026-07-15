# GROUP2 Survey

Hệ thống khảo sát Gen Z được dựng từ toàn bộ nội dung chính thức trong `docs/BoCauHoi.xlsx` và `docs/BỘ TIÊU CHÍ.xlsx`.

## Chạy nhanh

```bash
cd khaosat-api
cp .env.example .env
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

```bash
cd khaosat-web
cp .env.example .env
npm install
npm run dev
```

Không commit `.env`, SQLite, backup, media upload hoặc Firebase service-account. Trước production, hãy đổi `ADMIN_PASSWORD` trong `khaosat-api/.env`.

- Khảo sát: http://localhost:5173
- Admin: http://localhost:5173/admin (mật khẩu mặc định `admin123`)
- API docs: http://localhost:8000/docs

## Kiểm thử tự động

```bash
cd khaosat-api
make test
```

Backend tự import workbook ở lần chạy đầu. Có thể bấm **Đồng bộ Excel** trong admin để nạp lại toàn bộ sheet. Dữ liệu chạy lưu trong `khaosat-api/survey.db`.

## Kiến trúc

- `khaosat-web`: React + TypeScript + Vite + Redux Toolkit/RTK Query
- `khaosat-api`: FastAPI + SQLite + openpyxl
- Excel vẫn là nguồn tài liệu gốc; bảng `excel_sheets` giữ JSON của mọi sheet, còn câu hỏi/biến được chuẩn hoá để vận hành động.

Có thể đổi vị trí workbook khi triển khai bằng `SURVEY_WORKBOOK` và `CRITERIA_WORKBOOK`.
