import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials


def initialize_firebase():
    """Initialize Firebase Admin once without reading or uploading app data."""
    if firebase_admin._apps:
        return firebase_admin.get_app()

    credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not credential_path:
        return None

    path = Path(credential_path).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    if not path.is_file():
        raise RuntimeError(f"Không tìm thấy Firebase credential tại: {path}")

    options = {}
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    storage_bucket = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
    if project_id:
        options["projectId"] = project_id
    if storage_bucket:
        options["storageBucket"] = storage_bucket

    return firebase_admin.initialize_app(credentials.Certificate(path), options)
