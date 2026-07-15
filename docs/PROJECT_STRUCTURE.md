# Cấu trúc dự án MootCourt / E-Court

Tài liệu này giúp developer mới định vị codebase, hiểu cách frontend gọi backend, cách Redux/RTK Query quản lý dữ liệu và cách FastAPI tổ chức endpoint, service, model, database cùng các tích hợp ngoài.

## 1. Tổng quan repository

```text
n2nai_mootcourt/
├── ecourt-web/                 # Frontend React + TypeScript + Vite
├── ecourt-api/                 # Backend Python + FastAPI
├── docs/                       # BRD, SRS, RBAC, sơ đồ và tài liệu kỹ thuật
├── docs-site/                  # Website tài liệu Docusaurus
├── scratch/                    # File/script tạm phục vụ xử lý tài liệu
├── venv/                       # Python virtual environment cục bộ
└── README.md                   # README gốc của repository
```

Hai ứng dụng chính:

- `ecourt-web`: Single Page Application chạy trên trình duyệt.
- `ecourt-api`: REST API chứa nghiệp vụ, phân quyền, truy cập dữ liệu, AI/RAG và tích hợp.

Luồng request tổng quát:

```text
Browser
  → React Page/Component
  → Redux Toolkit / RTK Query
  → HTTP /backend-api
  → FastAPI Endpoint
  → Service
  → Database / Storage / External Service
  → JSON response
  → RTK Query cache
  → React UI
```

## 2. Frontend — `ecourt-web`

### 2.1 Công nghệ chính

| Nhóm | Công nghệ | Vai trò |
| --- | --- | --- |
| UI runtime | React 19, React DOM | Xây dựng giao diện theo component |
| Ngôn ngữ | TypeScript | Type safety cho component, state và API |
| Build/dev server | Vite 7 | Development server, build và bundling |
| Routing | React Router 6 | Public route, protected route và điều hướng SPA |
| UI library | Material UI 5, Emotion | Component, layout, theme và styling |
| State | Redux Toolkit, React Redux | Global state, đặc biệt là auth |
| Server state | RTK Query | Gọi API, cache, invalidation và loading/error state |
| HTTP bổ sung | Axios | Một số luồng HTTP không đi qua RTK Query |
| i18n | i18next, react-i18next | Giao diện tiếng Việt/tiếng Anh |
| Video | Daily JS | Courtroom video và participant runtime |
| Tài liệu | react-pdf, mammoth | Xem PDF và đọc/chuyển nội dung DOCX |
| Kiểm thử | Vitest | Unit test frontend |

### 2.2 Cấu trúc thư mục frontend

```text
ecourt-web/
├── public/                     # Logo, ảnh, PDF và static assets
├── src/
│   ├── assets/                 # Asset được import qua source code
│   ├── components/             # Component dùng chung và component nghiệp vụ
│   ├── config/                 # API, Firebase, module và site configuration
│   ├── hooks/                  # Typed Redux hooks, permission và helper hooks
│   ├── i18n/                   # Khởi tạo i18n và file dịch vi/en
│   ├── pages/                  # Màn hình cấp route
│   ├── store/                  # Redux store, slices và RTK Query API
│   ├── theme/                  # MUI theme, brand và section surfaces
│   ├── utils/                  # Permission, round label và business helpers
│   ├── App.tsx                 # Route tree và các route guard
│   ├── main.tsx                # Entry point React
│   └── index.css               # Global CSS
├── index.html                  # HTML shell cho Vite
├── package.json                # Dependencies và npm scripts
├── vite.config.ts              # Vite/Vitest/dev server configuration
└── tsconfig*.json              # TypeScript configuration
```

### 2.3 Entry point và route tree

`src/main.tsx` khởi tạo React root, import global CSS/i18n và render `App` trong `StrictMode`.

`src/App.tsx` chịu trách nhiệm:

- Bọc ứng dụng bằng Redux `Provider` và MUI `ThemeProvider`.
- Khai báo public route và authenticated route.
- Lazy-load các page để giảm kích thước bundle ban đầu.
- Áp dụng `ProtectedRoute`, `StudentStageGuard` và `CourtroomAccessGuard`.
- Render `Layout` chung cho khu vực đã đăng nhập.

Các nhóm route tiêu biểu:

| Route | Page | Phạm vi |
| --- | --- | --- |
| `/` | `PublicLandingPage` | Public |
| `/public/results` | `PublicResultsPage` | Public, chỉ dữ liệu đã công bố |
| `/login` | `LoginPage` | Authentication |
| `/dashboard` | `HomePage` | Người dùng đã đăng nhập |
| `/teams` | `TeamsPage` | Quản lý đội |
| `/cases` | `CasesPage` | Case, tài liệu, rubric và knowledge/RAG |
| `/r1-submission` | `R1SubmissionPage` | Student ở R1 |
| `/evidence` | `EvidencePage` | Student ở R1 |
| `/role-sheets` | `RoleSheetsPage` | Student ở R2/R3 |
| `/scoring` | `ScoringPage` | Judge/Secretary |
| `/courtroom` | `CourtroomPage` | Actor có quyền vào phiên |
| `/reports` | `ReportsPage` | Secretary/Support |

### 2.4 Pages và components

`src/pages` chứa component tương ứng với một màn hình/route. Page thường thực hiện các việc sau:

1. Đọc route params hoặc auth state.
2. Gọi RTK Query hook để tải dữ liệu.
3. Quản lý state chỉ thuộc màn hình.
4. Ghép các component nghiệp vụ.
5. Trigger mutation và hiển thị loading/error/success.

`src/components` chứa các khối tái sử dụng. Một số nhóm quan trọng:

- Layout/navigation: `Layout`, `PublicShell`, `PageHeader`, `Logo`.
- Access control: `ProtectedRoute`, `StudentStageGuard`, `CourtroomAccessGuard`.
- Global feedback: `ForbiddenToast`, deadline/countdown components.
- Courtroom: timeline, scoring dialog, Q&A panel, PDF presentation viewer.
- R1: intro video upload và submission-related components.

Quy ước: page điều phối dữ liệu và bố cục; component nên tập trung vào một trách nhiệm UI/nghiệp vụ cụ thể.

## 3. Redux Toolkit và RTK Query

### 3.1 Store

Store được khai báo tại `src/store/index.ts`:

```text
Redux Store
├── auth                         # authSlice: token và current user
└── api                          # apiSlice: RTK Query cache
```

Store đăng ký:

- `authReducer` cho client/global auth state.
- `apiSlice.reducer` cho server-state cache.
- `apiSlice.middleware` cho fetch lifecycle, cache và invalidation.
- `setupListeners` để hỗ trợ refetch theo focus/reconnect khi endpoint cấu hình.

Typed hooks nằm trong `src/hooks/redux.ts`, giúp component sử dụng `AppDispatch` và `RootState` an toàn bằng TypeScript.

### 3.2 Auth slice

`src/store/slices/authSlice.ts` quản lý:

- Access token.
- Thông tin user hiện tại.
- Login/logout và cập nhật credentials.
- Đồng bộ token với local storage theo implementation hiện tại.

Không dùng Redux state như lớp bảo mật. Frontend state chỉ phục vụ UX; backend vẫn phải xác minh token, role, ownership, assignment và resource state.

### 3.3 Base API slice

`src/store/api/apiSlice.ts` tạo RTK Query base API và xử lý cross-cutting behavior:

- Chọn API base URL từ biến môi trường, local backend hoặc remote fallback.
- Gắn Bearer token vào `Authorization` header.
- Thử API host tiếp theo khi gặp network/timeout error.
- Refresh token khi response là `401`.
- Deduplicate nhiều refresh request đồng thời bằng một shared promise.
- Phát event `ecourt:forbidden` khi gặp `403` để UI hiển thị toast.
- Khai báo tag như `Team`, `Case`, `Round`, `Evidence`, `Score`, `Courtroom` và `R1Packet`.

### 3.4 Domain API files

Mỗi domain inject endpoint vào base `apiSlice`:

```text
src/store/api/
├── apiSlice.ts                 # Base query, auth header, refresh và tag types
├── authApi.ts                  # Login, refresh, current user
├── usersApi.ts                 # User management
├── teamsApi.ts                 # Team management
├── casesApi.ts                 # Case operations
├── roundsApi.ts                # Round operations
├── documentsApi.ts             # Document upload/download/metadata
├── knowledgeLibraryApi.ts      # Knowledge library và RAG-related calls
├── memorialsApi.ts             # Memorial workflow
├── evidenceApi.ts              # Team evidence
├── sharedEvidenceApi.ts        # Shared evidence
├── r1SubmissionsApi.ts         # R1 packet workflow
├── scoringApi.ts               # Rubric, score và scorecard
├── bracketsApi.ts              # Bracket/match
├── judgeAssignmentsApi.ts      # Judge assignment
├── courtroomApi.ts             # Courtroom runtime
├── hearingMinutesApi.ts        # Biên bản phiên xử
├── reportsApi.ts               # Báo cáo, ranking và export
├── notificationsApi.ts         # Notification
└── achievementsApi.ts          # Achievement/certificate data
```

Tên file thực tế cần được kiểm tra trong thư mục khi thêm module; danh sách trên phản ánh các domain chính của repository.

### 3.5 Query, mutation và cache invalidation

Mẫu đọc dữ liệu:

```ts
const { data, isLoading, error } = useGetCasesQuery()
```

Mẫu thay đổi dữ liệu:

```ts
const [updateCase, { isLoading }] = useUpdateCaseMutation()
await updateCase(payload).unwrap()
```

Quy ước cache:

- Query cung cấp tag bằng `providesTags`.
- Mutation làm mất hiệu lực tag bằng `invalidatesTags`.
- Chỉ invalidate domain cần thiết; tránh refetch toàn bộ ứng dụng.
- Server state nên nằm trong RTK Query; chỉ đưa vào slice khi thật sự là client/global state.

## 4. Frontend configuration

### 4.1 API URL

`src/config/api.ts` tạo danh sách API URL theo thứ tự:

1. `VITE_API_BASE_URL`.
2. Local backend trong môi trường development.
3. `VITE_API_BASE_URL_FALLBACK`.
4. Remote API mặc định.

API chính sử dụng prefix `/backend-api` để không xung đột với route của SPA.

### 4.2 Vite

`vite.config.ts` cấu hình:

- React plugin.
- Dev server và preview trên port `30051`.
- `allowedHosts` cho local/domain dự án.
- Output build tại `dist`.
- Source map cho build.
- Vitest chạy các file `src/**/*.test.ts` và `src/**/*.test.tsx`.

### 4.3 Firebase và i18n

- `src/config/firebase.ts`: Firebase client configuration.
- `src/i18n/index.ts`: khởi tạo language detector và translation resources.
- `src/i18n/locales/vi.json`, `en.json`: text hiển thị theo ngôn ngữ.

Không hard-code secret vào frontend. Mọi biến `VITE_*` đều có thể xuất hiện trong bundle và chỉ nên chứa public configuration.

## 5. Backend — `ecourt-api`

### 5.1 Công nghệ chính

| Nhóm | Công nghệ | Vai trò |
| --- | --- | --- |
| API framework | FastAPI | REST API, dependency injection và OpenAPI |
| ASGI server | Uvicorn | Chạy FastAPI application |
| Validation | Pydantic v2 | Request/response schema và settings |
| Auth | python-jose, passlib/bcrypt | JWT và password hashing |
| Cloud auth/data | Firebase Admin, Firestore | Firebase integration và DB mặc định |
| DB tùy chọn | MongoDB, PyMongo | Backend dữ liệu khi `DB_BACKEND=mongo` |
| AI/RAG | Ollama, Anthropic, bge-m3, Qdrant | Chat/scoring, embedding và vector search |
| Documents | PyPDF2, python-docx, ReportLab | Extract và tạo tài liệu/báo cáo |
| Scheduling | APScheduler | Deadline reminder và scheduled jobs |
| Integration | Daily, SMTP, Web Push, Google Calendar | Video, email, push và lịch |
| Testing | pytest, httpx | Unit/integration/API tests |

### 5.2 Cấu trúc thư mục backend

```text
ecourt-api/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── api.py          # Tổng hợp router phiên bản v1
│   │       └── endpoints/      # FastAPI route handlers theo domain
│   ├── core/                   # Config, DB, auth, permission, storage, scheduler
│   ├── models/                 # Pydantic/domain models
│   └── services/               # Business logic theo domain
├── scripts/                    # Seed, migration, cleanup và vận hành
├── storage/                    # Local documents/images/videos
├── tests/                      # Backend test suite
├── main.py                     # FastAPI application entry point
├── requirements.txt            # Python dependencies
├── firestore.rules             # Firestore security rules
├── firestore.indexes.json      # Firestore indexes
├── firebase.json               # Firebase configuration
└── Dockerfile.converter        # Document converter container
```

### 5.3 FastAPI entry point

`main.py` thực hiện:

- Khởi tạo Firebase khi cấu hình hợp lệ.
- Tạo `FastAPI` application và OpenAPI endpoints.
- Cấu hình CORS whitelist.
- Reset request-scope cache cho mỗi request.
- Ghi thời gian response và log endpoint chậm hơn ngưỡng.
- Audit best-effort các response `403`.
- Mount API router tại `/backend-api`.
- Start/stop scheduler theo application lifecycle.
- Warm-up embedding model ở background khi dùng sentence-transformers.
- Cung cấp `/health` và redirect `/docs`.

Swagger UI nằm tại `/backend-api/docs`.

## 6. Backend layering

### 6.1 Endpoint layer

`app/api/v1/endpoints` là lớp HTTP. Mỗi file thường:

- Khai báo `APIRouter`.
- Định nghĩa path, HTTP method và response model.
- Đọc path/query/body/form/file input.
- Inject current user hoặc dependency phân quyền.
- Gọi service tương ứng.
- Chuyển lỗi nghiệp vụ thành HTTP response thích hợp.

`app/api/v1/api.py` gắn các router với prefix:

```text
/auth
/users
/teams
/cases
/rounds
/memorials
/evidence
/r1-submissions
/scoring
/brackets
/judge-assignments
/role-assignment-sheets
/courtrooms
/hearing-minutes
/documents
/knowledge-library
/reports
/notifications
/achievements
/system
/webhooks
/public
```

Endpoint không nên chứa truy vấn database hoặc business workflow dài. Những phần đó thuộc service.

### 6.2 Service layer

`app/services` là lớp nghiệp vụ. Ví dụ:

- `team_service.py`: CRUD và trạng thái đội.
- `team_auto_review_service.py`: OCR/auto-review đăng ký đội.
- `case_service.py`, `round_service.py`: case và vòng thi.
- `r1_submission_service.py`: packet, validation, submit và lock.
- `evidence_service.py`, `shared_evidence_service.py`: chứng cứ.
- `scoring_service.py`, `llm_scoring_service.py`: điểm, rubric và AI scoring.
- `bracket_service.py`: bracket và match transition.
- `judge_assignment_service.py`: phân công giám khảo.
- `courtroom_service.py`, `courtroom_macro_service.py`: courtroom runtime.
- `rag_service.py`, `qdrant_chunk_store.py`: retrieval và vector storage.
- `daily_service.py`: Daily room/recording.
- `notification_service.py`, `email_service.py`, `web_push_service.py`: thông báo.

`OwnershipAwareService` trong `app/core/service_base.py` cung cấp các helper dùng chung để kiểm tra quyền truy cập case/team, nộp memorial/evidence và chấm điểm.

### 6.3 Model layer

`app/models` chứa Pydantic/domain models. Các entity chính:

```text
User
Team
Case
Round
Document
KnowledgeLibraryItem
Memorial
R1Submission
EvidencePool / SharedEvidence
Rubric / Score / Scorecard
Bracket / Match
JudgeAssignment
RoleAssignmentSheet
Courtroom
HearingMinute
Notification
Achievement
AuditLog
```

Model được dùng để validate dữ liệu, tạo request/response contract và biểu diễn entity trong service.

Không nên đưa logic phụ thuộc HTTP vào model. Luật workflow phức tạp nên nằm trong service hoặc helper domain phù hợp.

## 7. Database layer

### 7.1 Database abstraction

`app/core/database.py` chọn backend dựa trên `settings.DB_BACKEND`:

```text
DB_BACKEND=firestore  → DatabaseService / Firestore
DB_BACKEND=mongo      → MongoDatabaseService / MongoDB
```

Interface chung cung cấp các thao tác như:

- `get(path)`
- `set(path, data)`
- `push(path, data)`
- `update(path, data)`
- `delete(path)`
- `get_all(collection)`
- `query_where(...)`
- `query_where_multi(...)`

`app/core/database_helpers.py` và request cache helpers giảm truy vấn lặp trong cùng request.

Khi thêm truy vấn:

- Ưu tiên interface dùng được cho cả Firestore và MongoDB.
- Không load toàn collection rồi filter nếu database có thể filter server-side.
- Kiểm tra index Firestore và index MongoDB.
- Test data scoping theo `case_id`, `team_id` và đặc biệt `match_id`.

### 7.2 Storage

Database lưu metadata; file được xử lý qua storage layer:

```text
app/core/storage.py
app/core/local_storage.py
storage/documents/
storage/images/
storage/videos/
```

Document conversion có thể gọi binary `soffice` hoặc converter service. File nhạy cảm có thể dùng document encryption nếu được cấu hình.

## 8. Auth, RBAC và ownership

Backend là nguồn sự thật cho authorization:

```text
JWT authentication
  → role permission
  → resource ownership/assignment
  → team/student stage
  → case/match scope
  → deadline/lock/publish state
```

Các file chính:

- `app/core/security.py`: token/security primitives.
- `app/core/dependencies.py`: FastAPI dependencies cho current user.
- `app/core/permissions.py`: permission theo role.
- `app/core/ownership.py`: ownership và assignment rules.
- `app/core/service_base.py`: ownership-aware assertions cho service.
- `docs/rbac-spec.md`: đặc tả RBAC và endpoint matrix.

Frontend guard chỉ giúp ẩn/hiện và điều hướng. Không được bỏ kiểm tra backend vì route hoặc button đã bị ẩn.

## 9. AI và RAG

Luồng RAG khái quát:

```text
PDF/DOCX
  → text_extractor
  → chunking
  → embedding (bge-m3)
  → Qdrant hoặc chunk store
  → semantic retrieval
  → prompt + rubric + retrieved context
  → Ollama hoặc Anthropic
  → score/explanation/metadata
```

Các file chính:

- `text_extractor.py`: lấy text từ tài liệu.
- `rag_service.py`: embedding và retrieval workflow.
- `qdrant_chunk_store.py`: Qdrant adapter.
- `llm_chat_provider.py`: abstraction cho chat provider.
- `llm_scoring_service.py`: chấm theo LLM/rubric.
- `ocr_provider.py`: OCR abstraction.

Các feature/config quan trọng gồm `AI_ENABLED`, `LLM_SCORING_ENABLED`, `RAG_EMBEDDING_PROVIDER`, `LLM_CHAT_PROVIDER`, Ollama/Anthropic model và Qdrant settings.

## 10. Tích hợp ngoài

| Tích hợp | Service/config | Mục đích |
| --- | --- | --- |
| Daily.co | `daily_service.py`, Daily config | Video courtroom, participant và recording |
| Daily webhook | `webhooks_daily.py` | Nhận recording/event bất đồng bộ |
| SMTP | `email_service.py` | Email notification |
| Web Push | `web_push_service.py`, VAPID | Browser push notification |
| Google Calendar | Calendar service/config | Lịch phiên và sự kiện |
| APScheduler | `scheduler.py`, `deadline_service.py` | Nhắc hạn và job định kỳ |
| Firebase | `firebase.py`, Firebase Admin | Auth/cloud và Firestore |
| LibreOffice | converter service/config | DOC/DOCX sang PDF |

Webhook và scheduled job phải idempotent vì có thể chạy hoặc được gửi lại nhiều lần.

## 11. Cách lần theo một tính năng

Khi cần hiểu hoặc sửa một chức năng, đi theo thứ tự:

```text
Route trong App.tsx
  → Page trong src/pages
  → Component liên quan
  → RTK Query hook trong src/store/api
  → FastAPI endpoint trong app/api/v1/endpoints
  → Service trong app/services
  → Model trong app/models
  → Database/storage/integration
  → Tests và BRD/SRS
```

Ví dụ với R1 submission:

```text
/r1-submission
  → R1SubmissionPage.tsx
  → r1SubmissionsApi.ts
  → /backend-api/r1-submissions
  → endpoints/r1_submissions.py
  → r1_submission_service.py
  → r1_submission.py model
  → DB + document/evidence storage
  → tests/test_memorial_autolock_row35.py và test liên quan
```

Ví dụ với courtroom:

```text
/courtroom
  → CourtroomAccessGuard + CourtroomPage
  → courtroom/presentation/scoring API hooks
  → /backend-api/courtrooms
  → endpoints/courtrooms.py
  → courtroom_service.py + daily_service.py
  → Courtroom/Match/JudgeAssignment models
  → DB + Daily webhook/recording
```

## 12. Thêm một module mới

Checklist đề xuất:

1. Xác định actor, permission, ownership và state transition.
2. Thêm hoặc cập nhật Pydantic model.
3. Viết service method chứa nghiệp vụ.
4. Thêm FastAPI endpoint và dependency auth.
5. Đăng ký router nếu là router mới.
6. Thêm RTK Query endpoints và tag invalidation.
7. Tạo page/component và route guard phù hợp.
8. Thêm i18n keys thay vì hard-code text mới.
9. Viết backend tests cho permission, validation và transition.
10. Viết frontend tests cho helper/guard quan trọng.
11. Cập nhật BRD/SRS/RBAC và migration/index nếu cần.

## 13. Chạy dự án cục bộ

### Frontend

```bash
cd ecourt-web
npm install
npm run dev
```

Các lệnh kiểm tra:

```bash
npm run build
npm run lint
npm test
```

Vite development server được cấu hình trên port `30051`.

### Backend

```bash
cd ecourt-api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 30951
```

Chạy test:

```bash
pytest
```

Lưu ý: `src/config/api.ts` hiện có local API mặc định riêng; hãy đặt `VITE_API_BASE_URL=http://127.0.0.1:30951/backend-api` nếu frontend không tự trỏ đúng backend đang chạy.

## 14. File nên đọc đầu tiên

Theo thứ tự onboarding:

1. `docs/PROJECT_STRUCTURE.md` — tài liệu hiện tại.
2. `docs/SRS_ECOURT_MOOTCOURT_FULL.md` — chức năng và entity.
3. `docs/SRS_ACTIVITY_SEQUENCE_DIAGRAMS.md` — activity/sequence flow.
4. `docs/rbac-spec.md` — role, permission và ownership.
5. `ecourt-web/src/App.tsx` — route và access guard.
6. `ecourt-web/src/store/api/apiSlice.ts` — HTTP/auth/cache frontend.
7. `ecourt-api/main.py` — application bootstrap.
8. `ecourt-api/app/api/v1/api.py` — danh sách API module.
9. `ecourt-api/app/core/config.py` — toàn bộ cấu hình runtime.
10. Service và test của module đang phụ trách.

## 15. Các nguyên tắc quan trọng

- Case không đồng nghĩa với Match; R2/R3 thường phải scope theo `match_id`.
- Role không đủ để cấp quyền; cần kiểm tra ownership hoặc assignment.
- Server state ưu tiên RTK Query; Redux slice chỉ dùng cho client/global state phù hợp.
- Endpoint giữ mỏng; business logic đặt trong service.
- Không truy cập database trực tiếp từ frontend.
- Không tin frontend guard như một security boundary.
- Submit, lock và publish là các transition khác nhau.
- AI output không mặc nhiên là điểm cuối cùng nếu workflow còn bước review.
- Firestore và MongoDB cần được giữ tương thích ở database abstraction.
- Secret, service-account key và `.env` không được commit vào repository.
