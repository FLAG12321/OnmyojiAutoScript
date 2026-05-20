import json
import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from module.device.device import Device


class RecordingDevice:
    """录制-回放机制的录制端。包装真实 Device，记录每一步操作。"""

    def __init__(self, device: Device, record_dir: Path):
        self._device = device
        self._record_dir = Path(record_dir)
        self._record_dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._actions: list[dict[str, Any]] = []
        self._screenshot_dir = self._record_dir

    @property
    def image(self) -> np.ndarray:
        return self._device.image

    def screenshot(self) -> np.ndarray:
        img = self._device.screenshot()
        self._seq += 1
        filename = f"{self._seq:04d}_screenshot.png"
        filepath = self._screenshot_dir / filename
        self._device.image_save(filepath)

        img_hash = hashlib.md5(img.tobytes()).hexdigest()[:12]
        self._actions.append({
            "seq": self._seq,
            "action": "screenshot",
            "file": filename,
            "hash": img_hash,
        })
        return img

    def click(self, x: int, y: int, control_check=True, control_name='Click') -> None:
        self._device.click(x, y, control_check=control_check, control_name=control_name)
        self._actions.append({
            "seq": self._seq,
            "action": "click",
            "x": int(x),
            "y": int(y),
            "target": str(control_name),
        })

    def swipe(self, p1, p2, duration=(0.1, 0.2), control_name='SWIPE', distance_check=True):
        self._device.swipe(p1, p2, duration=duration, control_name=control_name, distance_check=distance_check)
        self._actions.append({
            "seq": self._seq,
            "action": "swipe",
            "p1": [int(p1[0]), int(p1[1])],
            "p2": [int(p2[0]), int(p2[1])],
        })

    def app_start(self):
        self._device.app_start()

    def app_stop(self):
        self._device.app_stop()

    def save_actions(self) -> Path:
        """保存 actions.jsonl 并返回路径"""
        path = self._record_dir / "actions.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for action in self._actions:
                f.write(json.dumps(action, ensure_ascii=False) + "\n")
        return path

    @property
    def actions(self) -> list[dict]:
        return list(self._actions)
