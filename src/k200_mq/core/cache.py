"""데이터 캐싱 계층 — 중복 다운로드를 방지합니다.

Parquet 형식에 ``pyarrow`` 엔진과 ``zstd`` 압축을 사용하여
금융 DataFrame을 효율적으로 로컬 저장합니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


class DataCache:
    """Parquet 기반 로컬 캐시 — 금융 데이터를 저장합니다.

    각 캐시 키는 ``{cache_dir}/{key}.parquet``에 매핑됩니다.
    """

    def __init__(self, cache_dir: str = "data/raw") -> None:
        """캐시를 초기화하고 필요시 디렉토리를 생성합니다.

        Parameters
        ----------
        cache_dir : str
            Parquet 파일이 저장되는 루트 디렉토리
            (기본값 ``"data/raw"``).
        """
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ── 내부 헬퍼 ───────────────────────────────────────────────────────

    def _path(self, key: str) -> Path:
        """캐시 *key*에 대한 전체 파일 시스템 경로를 반환합니다."""
        return self._cache_dir / f"{key}.parquet"

    # ── 공개 API ─────────────────────────────────────────────────────

    def get(self, key: str) -> pd.DataFrame | None:
        """캐시된 DataFrame을 키로 불러옵니다.

        Parameters
        ----------
        key : str
            캐시 키 (``{cache_dir}/{key}.parquet``에 매핑).

        Returns
        -------
        pd.DataFrame or None
            캐시된 DataFrame이 존재하면 반환, 없으면 ``None``.
        """
        path = self._path(key)
        if not path.exists():
            return None
        return pd.read_parquet(path, engine="pyarrow")

    def put(self, key: str, df: pd.DataFrame) -> None:
        """DataFrame을 압축된 Parquet 파일로 캐시에 저장합니다.

        Parameters
        ----------
        key : str
            캐시 키.
        df : pd.DataFrame
            저장할 데이터.
        """
        path = self._path(key)
        df.to_parquet(path, engine="pyarrow", compression="zstd")

    def exists(self, key: str) -> bool:
        """캐시 *key*에 대한 항목이 존재하는지 확인합니다.

        Parameters
        ----------
        key : str
            캐시 키.

        Returns
        -------
        bool
            해당 Parquet 파일이 디스크에 존재하면 ``True``.
        """
        return self._path(key).exists()

    def _json_path(self, key: str) -> Path:
        """JSON 메타데이터 파일의 전체 경로를 반환합니다."""
        return self._cache_dir / f"{key}.json"

    def put_json(self, key: str, data: dict | list) -> None:
        """JSON 데이터를 캐시에 저장합니다.

        Parameters
        ----------
        key : str
            캐시 키 (``{cache_dir}/{key}.json``에 매핑).
        data : dict or list
            저장할 JSON 직렬화 가능 데이터.
        """
        path = self._json_path(key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_json(self, key: str) -> dict | list | None:
        """캐시된 JSON 데이터를 불러옵니다.

        Parameters
        ----------
        key : str
            캐시 키.

        Returns
        -------
        dict or list or None
            캐시된 JSON 데이터가 존재하면 반환, 없으면 ``None``.
        """
        path = self._json_path(key)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def clear(self) -> None:
        """캐시 디렉토리 내의 모든 캐시된 Parquet 파일을 삭제합니다.

        다른 파일(``.parquet``가 아닌 파일)은 그대로 유지됩니다.
        """
        for child in self._cache_dir.iterdir():
            if child.suffix == ".parquet":
                child.unlink()
