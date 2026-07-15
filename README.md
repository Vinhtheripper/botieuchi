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

## Deploy frontend lên Vercel

1. Import repository và đặt **Root Directory** là `khaosat-web`.
2. Thêm biến môi trường `VITE_API_BASE_URL=https://your-fastapi-domain.example/api` cho Production, Preview và Development nếu cần.
3. Deploy; `vercel.json` đã cấu hình Vite build, thư mục `dist` và SPA rewrite cho `/admin`.
4. Trong môi trường production của FastAPI, đặt `ALLOWED_ORIGINS` bằng domain Vercel thật. Có thể khai báo nhiều domain, phân tách bằng dấu phẩy.

Firebase Admin và service-account chỉ thuộc backend; tuyệt đối không đưa service-account vào Vercel frontend variables.

## Deploy backend lên Render

Có thể tạo Web Service thủ công hoặc dùng Blueprint `render.yaml` ở root repository. Với cấu hình thủ công:

- Root Directory: `khaosat-api`
- Runtime: Python 3
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health Check Path: `/api/health`

Trên Render, đặt `FIREBASE_CREDENTIALS_JSON` thành nguyên nội dung service-account JSON. Biến này là secret và không được đưa vào Git. Cách khác là tạo Secret File tên `firebase-service-account.json` rồi đặt `GOOGLE_APPLICATION_CREDENTIALS=/etc/secrets/firebase-service-account.json`.

`ALLOWED_ORIGINS` phải là domain frontend Vercel và `ADMIN_PASSWORD` phải là mật khẩu production mới. Backend hiện vẫn ghi respondent/answer vào SQLite; Render free không đảm bảo filesystem lâu dài, vì vậy cần chuyển dữ liệu sang Firebase hoặc gắn persistent disk trước khi thu dữ liệu thật.
