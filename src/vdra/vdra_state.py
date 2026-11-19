"""Local configuration, project bindings, and locked working-memory state."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import argparse
import os
import tempfile

import portalocker
import json
import re

from .focus_runtime import apply_record, checkpoint_focus
from .recall_policy import needs_resume
from .work_focus import set_focus

ROOT = (
    Path(os.environ.get("VDRA_HOME", str(Path.home() / ".vdra"))).expanduser().resolve()
)
CONFIG = ROOT / "work-memory.json"
_CONFIG_CACHE = (0, None)
STOP = re.compile(r"^(?:停止|取消|先别|暂停).{0,20}(?:任务|工作|操作|训练)?$")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def config():
    global _CONFIG_CACHE
    try:
        stamp = CONFIG.stat().st_mtime_ns
        if stamp != _CONFIG_CACHE[0]:
            _CONFIG_CACHE = (stamp, json.loads(CONFIG.read_text(encoding="utf-8")))
        return _CONFIG_CACHE[1]
    except (OSError, ValueError):
        return {"enabled": False}


def enabled(ctx, cfg=None):
    cfg = cfg or config()
    return bool(
        cfg.get("enabled")
        and ctx.account_id == cfg.get("account")
        and ctx.user.user_id == cfg.get("user")
    )


def clean(text):
    from .redaction import BLOCK, SECRET

    text = SECRET.sub("[credential redacted]", str(text or ""))
    text = BLOCK.sub("", text)
    text = re.sub(r"<heartbeat>.*?</heartbeat>", "", text, flags=re.S)
    text = re.sub(r"<!--\s*MEMORY_FIELDS\b.*?-->", "", text, flags=re.S)
    # Keep protected values protected. The long storage reference is not useful
    # context; source message IDs remain available for an authorized exact read.
    text = re.sub(r"\{\{\s*ov_privacy:[^{}]+\}\}", "[受保护字段；按来源消息查证]", text)
    # Match native deterministic credential rules too; do not weaken privacy.
    from openviking.privacy.rules import find_privacy_matches

    for hit in reversed(find_privacy_matches(text)):
        text = text[: hit.start] + "[private value omitted]" + text[hit.end :]
    return text.strip()


def atomic_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            name = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        name.replace(path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


def task_request(query):
    if needs_resume(query) or re.match(r"^我(?:说|指|问)的是", query):
        return False
    if STOP.fullmatch(query):
        return True
    action = re.search(
        r"实现|修复|排查|检查|设计|接入|导入|整理|优化|验证|改进|完善|做好|完成|封存|清理",
        query,
    )
    directive = re.match(
        r"^(?:请|帮|给|把|将|继续|现在|同时|先|做好|修复|检查|接入|导入|整理|优化|完成|完善|排查|让|希望|我希望|那就|不要)",
        query,
    )
    return bool(
        len(query) >= 12
        and action
        and directive
        and not re.match(r"^(?:为什么|怎么|如何|哪些|是否)", query)
    )


def _read_states():
    try:
        states = json.loads((ROOT / "work-state.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(states, dict):
        raise ValueError("work-state.json must contain an object")
    return states


def session_record(states, sid):
    state = states.get(sid, {})
    if not isinstance(state, dict) or not isinstance(state.get("served", {}), dict):
        raise ValueError("malformed work-memory session state")
    return state


@contextmanager
def state_lock():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = portalocker.Lock(
        ROOT / "work-state.lock", mode="a", timeout=0.5, check_interval=0.05
    )
    try:
        lock.acquire()
    except portalocker.exceptions.LockException as exc:
        raise TimeoutError("vdra state lock busy") from exc
    try:
        yield
    finally:
        lock.release()


def read_states():
    with state_lock():
        return _read_states()


@contextmanager
def state_transaction():
    with state_lock():
        states = _read_states()
        yield states
        atomic_json(ROOT / "work-state.json", states)


def mutate_state(sid, query, project):
    with state_transaction() as states:
        state = session_record(states, sid)
        state["recall_seq"] = int(state.get("recall_seq", 0)) + 1
        if task_request(query):
            request = clean(query)[:1200]
            if state.get("user_request") != request:
                state.pop("checkpoint", None)
                state = set_focus(state, goal=request)
            state["user_request"] = request
            state["request_at"] = datetime.now(timezone.utc).isoformat()
        if project:
            state["project"] = project
        states[sid] = state
    return state


def plain_sid(sid):
    return re.sub(r"^cx-", "", sid or "").split("-wm-")[0]


def choose_project(query, sid="", peer="", cfg=None):
    cfg = cfg or config()
    projects = cfg.get("projects", {})
    scored = []
    for key, p in projects.items():
        score = sum(
            max(1, len(alias))
            for alias in p.get("aliases", [])
            if alias.casefold() in query.casefold()
        )
        if score:
            scored.append((score, key))
    if scored:
        scored.sort(reverse=True)
        if len(scored) == 1 or scored[0][0] > scored[1][0]:
            return scored[0][1]
    bindings = cfg.get("session_projects", {})
    if plain_sid(sid) in bindings:
        return bindings[plain_sid(sid)]
    peers = [key for key, p in projects.items() if peer and peer in p.get("peers", [])]
    if len(peers) == 1:
        return peers[0]
    try:
        return session_record(read_states(), sid).get("project", "")
    except (OSError, ValueError):
        return ""


def record_served(sid, seq, signatures):
    if not signatures:
        return
    with state_transaction() as states:
        state = session_record(states, sid)
        served = {
            key: value
            for key, value in state.get("served", {}).items()
            if 0 <= seq - int(value) < 100
        }
        served.update({key: seq for key in signatures})
        state["served"] = dict(
            sorted(served.items(), key=lambda item: item[1], reverse=True)[:100]
        )
        states[sid] = state


def cli(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=["status", "state", "bind", "checkpoint", "focus", "issue", "link"],
    )
    parser.add_argument("--session")
    parser.add_argument("--project")
    parser.add_argument("--checkpoint-file")
    parser.add_argument("--record-file")
    args = parser.parse_args(argv)
    action = args.action

    if action == "status":
        cfg = config()
        result = {
            "enabled": cfg.get("enabled"),
            "native_working_memory": cfg.get("native_working_memory"),
            "projects": list(cfg.get("projects", {})),
            "aliases": cfg.get("session_aliases", {}),
        }
    elif action == "state":
        states = read_states()
        result = (
            states.get(args.session, {})
            if args.session
            else {"session_count": len(states)}
        )
    elif action == "bind":
        cfg = config()
        if not args.session or args.project not in cfg.get("projects", {}):
            parser.error("bind requires a session and configured project")
        with state_lock():
            new_config = json.loads(CONFIG.read_text(encoding="utf-8"))
            new_config.setdefault("session_projects", {})[plain_sid(args.session)] = (
                args.project
            )
            atomic_json(CONFIG, new_config)
        result = {"bound": args.session, "project": args.project}
    else:
        session = args.session or ""
        if not SESSION_ID.fullmatch(session):
            parser.error("valid session required")

        if action == "checkpoint":
            if not args.checkpoint_file:
                parser.error("checkpoint requires --checkpoint-file")
            data = json.loads(Path(args.checkpoint_file).read_text(encoding="utf-8"))
            if data["status"] not in {"active", "complete", "stopped"}:
                parser.error("invalid checkpoint status")
            if not (
                data["summary"]
                and data["verified_at"]
                and data["artifacts"]
                and isinstance(data.get("pending", []), list)
            ):
                parser.error("checkpoint requires a dated summary and evidence files")
            if not all(Path(item).is_file() for item in data["artifacts"]):
                parser.error("checkpoint evidence file is missing")
            data["summary"] = clean(data["summary"])[:1200]
            with state_transaction() as states:
                state = checkpoint_focus(session_record(states, session), session, data)
                state["checkpoint"] = data
                states[session] = state
            result = {"checkpoint": session, "status": data["status"]}
        else:
            if not args.record_file:
                parser.error(f"{action} requires --record-file")
            record = Path(args.record_file)
            if record.stat().st_size > 32768:
                parser.error("task record exceeds size limit")
            data = json.loads(record.read_text(encoding="utf-8"))
            with state_transaction() as states:
                states[session] = apply_record(
                    session_record(states, session), session, action, data, clean
                )
            result = {"recorded": action, "session": session}

    print(json.dumps(result, ensure_ascii=False))
