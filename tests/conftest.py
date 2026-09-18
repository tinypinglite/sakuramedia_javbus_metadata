from __future__ import annotations

import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = PLUGIN_ROOT.parent
host_root = os.environ.get("SAKURAMEDIA_HOST_ROOT")
if not host_root:
    raise RuntimeError("测试需要 SAKURAMEDIA_HOST_ROOT 指向宿主仓库")
HOST_ROOT = Path(host_root).expanduser()
if not (HOST_ROOT / "src/plugins/extensions/metadata.py").is_file():
    raise RuntimeError("SAKURAMEDIA_HOST_ROOT 不是有效的插件宿主仓库")
for path in (PLUGINS_ROOT, HOST_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
