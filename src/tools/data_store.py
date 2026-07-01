"""
Simple data store for tool seed data sets.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Any
from pathlib import Path

DEFAULT_DATA_SET = "default"
DATA_SET_ENV_VAR = "TOOL_DATA_SET"


class ToolDataStore:
    """Loads per-tool seed data from inline scenario definitions."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.active_set = os.getenv(DATA_SET_ENV_VAR, DEFAULT_DATA_SET)
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._inline_data: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self._bootstrap_default_inline()

    def _bootstrap_default_inline(self) -> None:
        default_path = os.path.join(self.base_dir, "default.json")
        if not os.path.exists(default_path):
            return
        try:
            with open(default_path, "r") as handle:
                payload = json.load(handle)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        collections = payload.get("data") or payload.get("collections") or {}
        if not isinstance(collections, dict):
            return
        try:
            self.set_inline_data(DEFAULT_DATA_SET, collections)
        except ValueError:
            return

    def set_active_set(self, data_set: str) -> None:
        if not data_set:
            raise ValueError("Data set name must be non-empty.")
        if data_set != self.active_set:
            self.active_set = data_set
            self._cache.clear()

    def set_inline_data(self, data_set: str, collections: Dict[str, Any]) -> None:
        if not data_set:
            raise ValueError("Inline data set name must be non-empty.")
        if not isinstance(collections, dict):
            raise ValueError("Inline data collections must be a dict.")
        normalized: Dict[str, List[Dict[str, Any]]] = {}
        for name, records in collections.items():
            if records is None:
                continue
            if not isinstance(records, list):
                raise ValueError(
                    f"Inline data for '{name}' must be a list, got {type(records).__name__}."
                )
            normalized[str(name)] = records
        self._inline_data[data_set] = normalized
        if data_set == self.active_set:
            self._cache.clear()

    def get_collection(self, name: str) -> List[Dict[str, Any]]:
        if name not in self._cache:
            self._cache[name] = self._load_collection(name)
        return self._cache[name]

    def _load_collection(self, name: str) -> List[Dict[str, Any]]:
        inline = self._inline_data.get(self.active_set, {})
        if name in inline:
            return inline[name]

        candidates = []
        if self.active_set:
            candidates.append(self.active_set)
            current = self.active_set
            while "/" in current:
                current = current.rsplit("/", 1)[0]
                candidates.append(current)
        candidates.append(DEFAULT_DATA_SET)

        for candidate in candidates:
            path = os.path.join(self.base_dir, candidate, f"{name}.json")
            if os.path.exists(path):
                with open(path, "r") as handle:
                    data = json.load(handle)
                if not isinstance(data, list):
                    raise ValueError(
                        f"Expected list data in {path}, got {type(data).__name__}."
                    )
                return data
        raise FileNotFoundError(
            f"No data set found for '{name}' in {self.base_dir}."
        )


_DATA_STORE = ToolDataStore(
    base_dir=str(Path(__file__).resolve().parents[2] / "scenarios" / "definitions")
)


def set_active_data_set(data_set: str) -> None:
    _DATA_STORE.set_active_set(data_set)


def set_inline_data_set(data_set: str, collections: Dict[str, Any]) -> None:
    _DATA_STORE.set_inline_data(data_set, collections)


def get_collection(name: str) -> List[Dict[str, Any]]:
    return _DATA_STORE.get_collection(name)


def get_active_data_set() -> str:
    return _DATA_STORE.active_set
