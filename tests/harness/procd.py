#!/usr/bin/env python3
"""Process bridge for the offscreen QML harness.

The plain `qml` runtime cannot spawn processes, so the stub
Quickshell.Io.Process writes `<bridge>/req/<id>.json` and polls for
`<bridge>/res/<id>.json`. This daemon runs each request for real and writes
the result back. It exits when `<bridge>/stop` appears or its parent dies.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time


def run_request(bridge, name, live):
    path = os.path.join(bridge, "req", name)
    try:
        with open(path, encoding="utf-8") as handle:
            req = json.load(handle)
    except (OSError, ValueError):
        return False  # still being written; retry on the next sweep

    rid = req.get("id") or name[:-5]
    env = dict(os.environ)
    for key, value in (req.get("environment") or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)

    result = {"id": rid, "code": 127, "stdout": "", "stderr": ""}
    try:
        proc = subprocess.Popen(
            [str(part) for part in req.get("command") or []],
            cwd=req.get("cwd") or None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        live[rid] = proc
        out, err = proc.communicate()
        result["code"] = proc.returncode
        result["stdout"] = out.decode("utf-8", "replace")
        result["stderr"] = err.decode("utf-8", "replace")
    except OSError as error:
        result["stderr"] = str(error)
    finally:
        live.pop(rid, None)

    with open(os.path.join(bridge, "log.jsonl"), "a", encoding="utf-8") as log:
        log.write(json.dumps({
            "command": req.get("command"),
            "code": result["code"],
            "stdout_bytes": len(result["stdout"]),
            "stderr": result["stderr"][-2000:],
        }) + "\n")

    tmp = os.path.join(bridge, "res", rid + ".json.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    os.replace(tmp, os.path.join(bridge, "res", rid + ".json"))
    return True


def main():
    if len(sys.argv) < 2:
        print("usage: procd.py <bridge-dir> [parent-pid]", file=sys.stderr)
        return 64
    bridge = sys.argv[1]
    parent = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    for sub in ("req", "res", "kill"):
        os.makedirs(os.path.join(bridge, sub), exist_ok=True)

    seen = set()
    live = {}
    lock = threading.Lock()

    def worker(name):
        while not run_request(bridge, name, live):
            time.sleep(0.005)

    while not os.path.exists(os.path.join(bridge, "stop")):
        if parent and not os.path.exists("/proc/%d" % parent):
            break
        for name in sorted(os.listdir(os.path.join(bridge, "req"))):
            if not name.endswith(".json"):
                continue
            with lock:
                if name in seen:
                    continue
                seen.add(name)
            threading.Thread(target=worker, args=(name,), daemon=True).start()
        for rid in os.listdir(os.path.join(bridge, "kill")):
            proc = live.get(rid)
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
        time.sleep(0.005)

    for proc in list(live.values()):
        if proc.poll() is None:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
