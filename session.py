"""同時視聴（一緒に見る）のセッション状態。

Activity は参加者ごとに別々の iframe が動くので、そのままでは各自が
好きに操作する。Discord から渡される instance_id が「同じ通話で開いた
一群」を指すので、それを鍵にサーバー側で状態を共有する。

共有するのは再生位置だけではなく**画面の状態そのもの**（検索語・絞り込み・
並び順・開いているクリップ・スクロール位置）。ホストの画面をそのまま
参加者に映す。

状態は揮発でよい（通話が終われば消えてよい）ため DB は使わない。
"""
import asyncio
import time
from typing import Optional

HOST_TIMEOUT = 20.0       # ホストからの更新が途絶えたら解散
WATCHER_TIMEOUT = 15.0    # 見ている人の生存期限
SESSION_TIMEOUT = 1800.0  # 放置されたセッションの掃除

_sessions: dict[str, dict] = {}
_events: dict[str, asyncio.Event] = {}

# ホストが配る画面の状態。想定外のキーは捨てる。
VIEW_KEYS: dict[str, type] = {
    "query": str, "sort": str, "game": str, "tag": str, "by": str,
    "onlyFav": bool, "loaded": int,
    "openId": int, "position": float, "playing": bool, "scroll": int,
}

# 一覧の見え方を決めるキー。ここが変わったら参加者は作り直す。
LAYOUT_KEYS = ("query", "sort", "game", "tag", "by", "onlyFav", "loaded", "openId")


def clean_view(raw) -> dict:
    """受け取った状態を既知のキーだけに絞る。"""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key, cast in VIEW_KEYS.items():
        if key not in raw or raw[key] is None:
            continue
        try:
            value = cast(raw[key])
        except (TypeError, ValueError):
            continue
        if cast is str:
            value = value[:120]
        out[key] = value
    return out


def _new(instance: str) -> dict:
    return {"instance": instance, "host_id": None, "host_name": "",
            "view": {}, "rev": 0, "tick": 0, "view_at": 0.0,
            "updated_at": 0.0, "watchers": {}}


def _wake(instance: str) -> None:
    """待っている参加者を起こす。"""
    ev = _events.get(instance)
    if ev is not None:
        ev.set()
        ev.clear()


async def wait_change(instance: str, since: int, timeout: float = 20.0) -> None:
    """状態が変わるまで待つ。

    毎秒問い合わせるより、変化した瞬間に返すほうが速く、通信も少ない。
    """
    s = _sessions.get(instance)
    if s is not None and s["tick"] != since:
        return
    ev = _events.setdefault(instance, asyncio.Event())
    try:
        await asyncio.wait_for(ev.wait(), timeout)
    except asyncio.TimeoutError:
        pass


def _sweep(now: float) -> None:
    for key, s in list(_sessions.items()):
        for uid, w in list(s["watchers"].items()):
            if now - w["seen"] > WATCHER_TIMEOUT:
                del s["watchers"][uid]
        if s["host_id"] is not None and now - s["updated_at"] > HOST_TIMEOUT:
            s["host_id"] = None
            s["host_name"] = ""
            s["view"] = {}
            s["tick"] += 1
            _wake(key)
        if not s["watchers"] and now - s["updated_at"] > SESSION_TIMEOUT:
            del _sessions[key]


def _shape(s: dict, me: int) -> dict:
    return {
        "instance": s["instance"],
        "host_id": str(s["host_id"]) if s["host_id"] else None,
        "host_name": s["host_name"],
        "is_host": s["host_id"] == me,
        "view": s["view"],
        "rev": s["rev"],
        "tick": s["tick"],
        # ホストが位置を送ってから何秒経ったか。参加者はこれを足して補正する。
        "elapsed": round(max(0.0, time.time() - s["view_at"]), 3)
                   if s.get("view_at") else 0.0,
        "age": round(time.time() - s["updated_at"], 2) if s["updated_at"] else None,
        "watchers": [w["name"] for w in s["watchers"].values()],
    }


def touch(instance: str, user_id: int, user_name: str) -> dict:
    """状態を読みつつ、自分が見ていることを登録する。"""
    now = time.time()
    _sweep(now)
    s = _sessions.setdefault(instance, _new(instance))
    s["watchers"][user_id] = {"name": user_name, "seen": now}
    return _shape(s, user_id)


def take_host(instance: str, user_id: int, user_name: str, take: bool) -> dict:
    now = time.time()
    _sweep(now)
    s = _sessions.setdefault(instance, _new(instance))
    if take:
        # 前のホストが沈黙していれば引き継げる
        if s["host_id"] in (None, user_id) or now - s["updated_at"] > HOST_TIMEOUT:
            s["host_id"] = user_id
            s["host_name"] = user_name
            s["updated_at"] = now
    elif s["host_id"] == user_id:
        s["host_id"] = None
        s["host_name"] = ""
        s["view"] = {}
    s["watchers"][user_id] = {"name": user_name, "seen": now}
    s["tick"] += 1
    _wake(instance)
    return _shape(s, user_id)


def push(instance: str, user_id: int, user_name: str, view) -> dict:
    """ホストが自分の画面の状態を配る。"""
    now = time.time()
    _sweep(now)
    s = _sessions.setdefault(instance, _new(instance))
    if s["host_id"] not in (None, user_id) and now - s["updated_at"] <= HOST_TIMEOUT:
        return _shape(s, user_id)          # 他人がホスト中なら無視
    view = clean_view(view)

    # 再生位置とスクロールは常時動くので rev の判定には使わない。
    # 参加者はここが変わったときだけ一覧を作り直す。
    before = {k: s["view"].get(k) for k in LAYOUT_KEYS}
    after = {k: view.get(k) for k in LAYOUT_KEYS}
    if before != after:
        s["rev"] += 1

    s["host_id"] = user_id
    s["host_name"] = user_name
    s["view"] = view
    s["view_at"] = now          # この位置がいつの時点のものか
    s["updated_at"] = now
    s["watchers"][user_id] = {"name": user_name, "seen": now}
    s["tick"] += 1
    _wake(instance)
    return _shape(s, user_id)


def host_of(instance: str) -> Optional[int]:
    """今そのセッションでホストになっている人。お気に入りの参照先に使う。"""
    s = _sessions.get(instance)
    if not s:
        return None
    if s["host_id"] is None:
        return None
    if time.time() - s["updated_at"] > HOST_TIMEOUT:
        return None
    return s["host_id"]


def stats() -> dict:
    _sweep(time.time())
    return {"sessions": len(_sessions),
            "watchers": sum(len(s["watchers"]) for s in _sessions.values())}
