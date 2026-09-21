"""빌드 입출력 경로. 원문은 ref/, 산출물은 apps/api/kb_artifacts/<build_id>/ 에 둔다."""

from __future__ import annotations

import hashlib
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = API_DIR.parents[1]
REF_DIR = REPO_DIR / "ref"
ARTIFACTS_DIR = API_DIR / "kb_artifacts"

LAW_FILE = "전자상거래 등에서의 소비자보호에 관한 법률(법률)(제21312호)(20260721).doc"
STANDARD_FILE = "소비자분쟁해결기준(공정거래위원회고시)(제2025-14호)(20251218).doc"
APPENDIX_FILE = "별표-소비자분쟁해결기준(공정거래위원회고시)(제2025-14호)(20251218).pdf"

LAW_NAME = "전자상거래법"


def source_paths() -> dict[str, Path]:
    return {
        "law": REF_DIR / LAW_FILE,
        "standard": REF_DIR / STANDARD_FILE,
        "appendix": REF_DIR / APPENDIX_FILE,
    }


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def build_dir(build_id: str) -> Path:
    directory = ARTIFACTS_DIR / build_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory
