import argparse
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from . import state
from .helpers import (
    _available_config_files,
    _capture_frame,
    _debug_frame_path,
    _detection_csv_path,
    _event_log_path,
    _events_root,
    _inference_log_dir,
    _load_config,
    _per_id_video_root,
    _read_csv_tail,
)
from .inference import (
    _get_default_inference_manager,
    _get_inference_manager_for_key,
    startup_default_manager,
    shutdown_all_inference_managers,
)
from .models import (
    ConfigPayload,
    ConfigSaveAsPayload,
    ConfigSelectPayload,
    FlowVector,
    ZonePayload,
)

app = FastAPI(title='CleaningCar Zone Editor')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.on_event('startup')
def _startup_manager():
    try:
        startup_default_manager()
    except Exception as exc:
        print(f'[server] startup: failed to init default inference manager: {exc}')


@app.on_event('shutdown')
def _shutdown_manager():
    shutdown_all_inference_managers()

@app.get("/", response_class=HTMLResponse)
def index():
    if not state.TEMPLATE_PATH.exists():
        raise HTTPException(status_code=500, detail="Template missing.")
    return state.TEMPLATE_PATH.read_text(encoding="utf-8")


@app.get("/static/vue.global.prod.js")
def vue_bundle():
    path = state.ROOT / "web" / "static" / "vue.global.prod.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Vue bundle missing.")
    return FileResponse(path)


@app.get("/static/{name}")
def static_file(name: str):
    path = state.ROOT / "web" / "static" / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Static file missing.")
    return FileResponse(path)


@app.get("/zones")
def read_zones():
    cfg = _load_config()
    return cfg.zones


@app.post("/zones")
def update_zones(payload: ZonePayload):
    cfg = _load_config()
    cfg.data.setdefault("zones", {})
    cfg.data["zones"]["zone_a_detection"] = payload.zone_a_detection
    cfg.data["zones"]["zone_b_wash"] = payload.zone_b_wash
    cfg.data["zones"]["flow_vector"] = {
        "start": payload.flow_vector.start,
        "end": payload.flow_vector.end,
    }
    cfg.save()
    return {"status": "ok"}


@app.get("/frame_meta")
def frame_meta():
    if state.FRAME_CACHE.data is None:
        try:
            _capture_frame()
        except HTTPException:
            return {"available": False}
    w, h = state.FRAME_CACHE.size
    return {"available": True, "width": w, "height": h}


@app.get("/frame")
def get_frame():
    if state.FRAME_CACHE.data is None:
        _capture_frame()
    return Response(content=state.FRAME_CACHE.data, media_type="image/jpeg")


@app.post("/frame/reload")
def reload_frame():
    _capture_frame(force=True)
    return {"status": "ok"}


@app.get("/debug_frame_meta")
def debug_frame_meta():
    cfg = _load_config()
    path = _debug_frame_path(cfg)
    if not path or not path.exists():
        return {"available": False}
    try:
        stat = path.stat()
        return {"available": True, "path": str(path), "updated": stat.st_mtime}
    except Exception:
        return {"available": True, "path": str(path), "updated": 0}


@app.get("/debug_frame")
def debug_frame():
    cfg = _load_config()
    path = _debug_frame_path(cfg)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="调试帧不存在，请先在配置里启用 debug_frame_path。")
    try:
        data = path.read_bytes()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法读取调试帧: {exc}") from exc
    return Response(content=data, media_type="image/jpeg")


@app.get("/videos/per_id")
def list_per_id_videos(limit: int = 200):
    cfg = _load_config()
    root = _per_id_video_root(cfg)
    events_root = _events_root(cfg)
    if not root.exists():
        return {"videos": []}
    try:
        files = sorted(root.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return {"videos": []}
    items = []
    limit = max(1, min(int(limit), 500))
    for path in files[:limit]:
        try:
            stat = path.stat()
        except Exception:
            continue
        rel = str(path.relative_to(root))
        name = path.name
        stem = path.stem
        camera_id = None
        track_id = None
        meta = {}
        parts = stem.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            camera_id = parts[0]
            track_id = int(parts[1])
            key_prefix = f"{camera_id}_{track_id}"
        else:
            dash_parts = stem.rsplit("-", 2)
            if len(dash_parts) == 3 and dash_parts[2].isdigit():
                camera_id = dash_parts[0]
                track_id = int(dash_parts[2])
                key_prefix = f"{camera_id}_{track_id}"
            else:
                key_prefix = None
        if key_prefix:
            try:
                cand = sorted(events_root.glob(f"{key_prefix}_t5_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            except Exception:
                cand = []
            if cand:
                try:
                    with cand[0].open("r", encoding="utf-8") as f:
                        event = json.load(f)
                except Exception:
                    event = {}
                meta = {
                    "plateNumber": event.get("plateNumber") or "",
                    "captureTime": event.get("captureTime") or "",
                    "isAbnormal": bool(event.get("isAbnormal")),
                    "abnormalReason": event.get("abnormalReason") or "",
                    "vehicleType": event.get("vehicleType") or "",
                    "lane": event.get("lane") or "",
                }
        items.append({
            "file": name,
            "relativePath": rel,
            "cameraId": camera_id,
            "trackId": track_id,
            "meta": meta,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "session": str(path.parent.relative_to(root)) if path.parent != root else "",
        })
    return {"videos": items}


@app.get("/videos/per_id/file")
def get_per_id_video(path: str):
    cfg = _load_config()
    root = _per_id_video_root(cfg)
    target = (root / path).resolve()
    try:
        root_resolved = root.resolve()
    except Exception:
        root_resolved = root
    if not str(target).startswith(str(root_resolved)):
        raise HTTPException(status_code=403, detail="无效路径")
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(target), media_type="video/mp4", filename=target.name)


@app.get("/config")
def read_config():
    cfg = _load_config()
    return cfg.data


@app.post("/config/save_as")

def save_config_as(payload: ConfigSaveAsPayload):
    name = str(payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    if any(sep in name for sep in ("/", "\\")):
        raise HTTPException(status_code=400, detail="文件名不能包含路径分隔符")
    if not name.lower().endswith(".json"):
        name = f"{name}.json"
    base = state.CONFIG_PATH.parent
    target = (base / name).resolve()
    if target.exists():
        raise HTTPException(status_code=409, detail="配置文件已存在")
    try:
        with target.open("w", encoding="utf-8") as f:
            json.dump(payload.data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存失败: {exc}") from exc
    return {"saved_as": target.name}

@app.get("/config/files")
def list_config_files():
    files = _available_config_files()
    return {
        "active": state.CONFIG_PATH.name,
        "files": [p.name for p in files],
    }


@app.post("/config/select")
def select_config(payload: ConfigSelectPayload):
    base = state.CONFIG_PATH.parent
    candidate = (base / payload.name).resolve()
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="配置文件不存在")
    if candidate.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="仅支持 JSON 配置")
    state.set_config_path(candidate)
    mgr = _get_default_inference_manager()
    mgr.set_config_path(candidate)
    state.FRAME_CACHE.clear()
    return {"active": candidate.name}

@app.post("/config")
def update_config(payload: ConfigPayload):
    cfg = _load_config()
    if payload.system:
        cfg.data.setdefault("system", {}).update(payload.system)
    if payload.video:
        cfg.data.setdefault("video", {}).update(payload.video)
        state.FRAME_CACHE.clear()
    if payload.logic:
        cfg.data.setdefault("logic", {}).update(payload.logic)
    cfg.save()
    return {"status": "ok"}


@app.post("/inference/start")
def start_inference(key: Optional[str] = None):
    mgr = _get_inference_manager_for_key(key or state.CONFIG_PATH.name)
    return mgr.start()


@app.post("/inference/stop")
def stop_inference(key: Optional[str] = None):
    mgr = _get_inference_manager_for_key(key or state.CONFIG_PATH.name)
    return mgr.stop()


@app.post("/inference/restart")
def restart_inference(key: Optional[str] = None):
    mgr = _get_inference_manager_for_key(key or state.CONFIG_PATH.name)
    return mgr.restart()


@app.post("/inference/auto_restart")
def set_auto_restart(enable: bool = True, key: Optional[str] = None):
    mgr = _get_inference_manager_for_key(key or state.CONFIG_PATH.name)
    mgr.set_auto_restart(enable)
    return {"auto_restart": enable}


@app.get("/inference/status")
def inference_status(key: Optional[str] = None):
    mgr = _get_inference_manager_for_key(key or state.CONFIG_PATH.name)
    return mgr.status()


@app.get("/logs/inference")
def inference_logs(lines: int = 200, key: Optional[str] = None):
    mgr = _get_inference_manager_for_key(key or state.CONFIG_PATH.name)
    return {"lines": mgr.logs(lines)}


@app.get("/logs/events")
def get_event_logs(lines: int = 50):
    cfg = _load_config()
    path = _event_log_path(cfg)
    if not path.exists():
        return {"available": False, "path": str(path)}
    rows = _read_csv_tail(path, lines)
    return {"available": True, "path": str(path), "rows": rows}


@app.get("/logs/detections")
def get_detection_logs(lines: int = 50):
    cfg = _load_config()
    csv_path = _detection_csv_path(cfg)
    if not csv_path or not csv_path.exists():
        return {"available": False, "path": str(csv_path) if csv_path else ""}
    rows = _read_csv_tail(csv_path, lines)
    return {"available": True, "path": str(csv_path), "rows": rows}


@app.get("/logs/files")
def list_inference_log_files():
    base = _inference_log_dir()
    if not base.exists():
        return {"files": []}
    items = []
    for p in sorted(base.glob("*.log")):
        try:
            st = p.stat()
        except OSError:
            continue
        items.append(
            {
                "name": p.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        )
    return {"files": items}


@app.get("/logs/file/{name}")
def read_inference_log_file(name: str, lines: int = 400):
    if any(sep in name for sep in ("/", "\\")):
        raise HTTPException(status_code=400, detail="文件名非法")
    base = _inference_log_dir()
    path = (base / name).resolve()
    try:
        base_resolved = base.resolve()
    except Exception:
        base_resolved = base
    if not str(path).startswith(str(base_resolved)):
        raise HTTPException(status_code=400, detail="路径越界")
    if not path.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")
    max_lines = max(1, min(2000, int(lines)))
    buf = deque(maxlen=max_lines)
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                buf.append(line.rstrip("\n"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取失败: {exc}") from exc
    content = "\n".join(buf)
    return PlainTextResponse(content)


@app.get("/logs", response_class=HTMLResponse)
def logs_page():
    html = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>CleaningCar 日志监控</title>
  <style>
    body { font-family: "Segoe UI", "PingFang SC", sans-serif; margin: 0; background: #111; color: #eee; }
    header { padding: 12px 20px; background: #191d23; border-bottom: 1px solid #242931; }
    main { padding: 16px 20px 40px; display: grid; grid-template-columns: 260px 1fr; gap: 16px; }
    h1 { margin: 0; font-size: 18px; }
    .sidebar { background: #1c2129; border: 1px solid #2a313d; border-radius: 8px; padding: 10px; }
    .content { background: #0f1115; border: 1px solid #2a313d; border-radius: 8px; padding: 10px; }
    ul { list-style: none; padding: 0; margin: 0; max-height: 70vh; overflow-y: auto; }
    li { padding: 6px 8px; cursor: pointer; border-radius: 4px; font-size: 13px; }
    li:hover { background: #2a313d; }
    li.active { background: #345; }
    .meta { font-size: 11px; color: #9aa3b5; }
    pre { white-space: pre-wrap; word-break: break-all; font-size: 12px; line-height: 1.4; }
    .toolbar { margin-bottom: 8px; font-size: 13px; display: flex; gap: 8px; align-items: center; }
    input { background: #000; border-radius: 4px; border: 1px solid #2a313d; color: #eee; padding: 4px 6px; width: 80px; }
    button { border: none; border-radius: 4px; padding: 4px 10px; background: #2f7cf8; color: #fff; cursor: pointer; font-size: 13px; }
    button.secondary { background: #3b3f45; }
    a { color: #8ab4ff; text-decoration: none; }
  </style>
</head>
<body>
  <header>
    <h1>CleaningCar 日志监控</h1>
  </header>
  <main>
    <section class="sidebar">
      <div class="toolbar">
        <span>推理日志文件</span>
        <button class="secondary" onclick="loadFiles()">刷新</button>
      </div>
      <ul id="file-list"></ul>
    </section>
    <section class="content">
      <div class="toolbar">
        <span id="current-file">未选择文件</span>
        <span style="flex:1"></span>
        <label>尾部行数 <input id="line-count" type="number" min="50" max="2000" value="400"></label>
        <button onclick="reloadContent()">刷新内容</button>
        <a href="/" style="margin-left:8px;">返回配置控制台</a>
      </div>
      <pre id="log-content">选择左侧日志文件查看内容。</pre>
    </section>
  </main>
  <script>
    let current = null;
    async function loadFiles() {
      const ul = document.getElementById('file-list');
      ul.innerHTML = '<li>加载中…</li>';
      try {
        const res = await fetch('/logs/files');
        if (!res.ok) throw new Error('请求失败');
        const data = await res.json();
        const files = data.files || [];
        if (!files.length) {
          ul.innerHTML = '<li>暂无日志文件</li>';
          return;
        }
        ul.innerHTML = '';
        files.sort((a, b) => b.mtime - a.mtime);
        for (const f of files) {
          const li = document.createElement('li');
          li.textContent = f.name;
          li.onclick = () => selectFile(f.name, li);
          const meta = document.createElement('div');
          meta.className = 'meta';
          const date = new Date(f.mtime * 1000);
          meta.textContent = date.toLocaleString() + ' · ' + f.size + ' bytes';
          li.appendChild(meta);
          ul.appendChild(li);
        }
      } catch (err) {
        ul.innerHTML = '<li>加载失败: ' + err.message + '</li>';
      }
    }
    async function selectFile(name, li) {
      current = name;
      document.getElementById('current-file').textContent = name;
      const items = document.querySelectorAll('#file-list li');
      items.forEach(x => x.classList.remove('active'));
      li.classList.add('active');
      await reloadContent();
    }
    async function reloadContent() {
      const pre = document.getElementById('log-content');
      if (!current) {
        pre.textContent = '请选择日志文件。';
        return;
      }
      const n = document.getElementById('line-count').value || '400';
      pre.textContent = '加载中…';
      try {
        const res = await fetch('/logs/file/' + encodeURIComponent(current) + '?lines=' + encodeURIComponent(n));
        if (!res.ok) throw new Error('请求失败');
        const text = await res.text();
        pre.textContent = text || '(空文件)';
      } catch (err) {
        pre.textContent = '读取失败: ' + err.message;
      }
    }
    loadFiles();
  </script>
</body>
</html>
    """
    return HTMLResponse(html)


def parse_args():
    parser = argparse.ArgumentParser(description='CleaningCar FastAPI server')
    parser.add_argument('--config', default=str(state.CONFIG_PATH), help='config.json path')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--reload', action='store_true', help='enable uvicorn reload')
    return parser.parse_args()


def main():
    import uvicorn

    args = parse_args()
    state.set_config_path(Path(args.config).resolve())
    uvicorn.run(
        'web.server:app',
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level='warning',
        access_log=False,
    )


if __name__ == '__main__':
    main()
