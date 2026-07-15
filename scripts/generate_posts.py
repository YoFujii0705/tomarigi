#!/usr/bin/env python3
"""とまり木: 住人の投稿を生成する。GitHub Actionsから定時実行される。"""
import json, os, random, re, sys, glob
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
import requests

ROOT = Path(__file__).resolve().parent.parent
POSTS_PATH = ROOT / "docs" / "posts.json"
JST = timezone(timedelta(hours=9))

API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.environ.get("TOMARIGI_MODEL", "claude-haiku-4-5-20251001")
MAX_ACTIVE_PER_RUN = int(os.environ.get("TOMARIGI_MAX_ACTIVE", "3"))
MAX_LEN = 140

COMMON_RULES = """あなたは小さな架空のSNS「とまり木」の住人のひとりを演じます。以下は絶対の掟です。
- 読者に向かって話さない。誰かに見せるために書かない。説明しない。
- 面白くあろうとしない。オチをつけようとしない。気の利いたことを言おうとしない。
- 実際のSNSの投稿のような、生活の断面の凡庸さを大切にする。
- ハッシュタグ、絵文字の多用、「皆さん」等の呼びかけは禁止。
- 現実の固有名詞(商品、駅、番組、本など)は使ってよい。ただし実在の個人名は出さない。
- 140字以内。途中で切れない、自然に終わる短文にする。
- 過去の自分の投稿と同じ話題を繰り返しすぎない。生活は少しずつ進む。"""


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_posts():
    if POSTS_PATH.exists():
        return json.loads(POSTS_PATH.read_text(encoding="utf-8"))
    return {"posts": []}


def save_posts(data):
    POSTS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def in_window(hour, windows):
    return any(start <= hour < end for start, end in windows)


def pick_active(personas, hour):
    """時間帯と頻度から、この回に動く住人を確率で選ぶ。"""
    active = []
    for p in personas:
        w = p["posting"]["windows"]
        if not in_window(hour, w):
            continue
        window_hours = sum(e - s for s, e in w)
        runs_in_window = max(1, window_hours // 1)  # 1時間おき起動の想定
        prob = min(1.0, p["posting"]["posts_per_day"] / runs_in_window * 0.6)
        if random.random() < prob:
            active.append(p)
    random.shuffle(active)
    return active[:MAX_ACTIVE_PER_RUN]


def relationship_notes(rels, me):
    lines = []
    for e in rels.get("edges", []):
        if e["from"] == me["id"]:
            lines.append(f"- あなた→{e['to']}: {e['note']}")
    return "\n".join(lines) if lines else "(特記事項なし)"


def call_claude(system, user):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 700,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=120,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")


def extract_json(text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"JSONが見つからない: {text[:200]}")
    return json.loads(m.group(0))


def format_timeline(posts, personas_by_id, limit=25):
    recent = posts[-limit:]
    lines = []
    for p in recent:
        author = personas_by_id.get(p["author"], {}).get("name", p["author"])
        prefix = ""
        if p.get("reply_to"):
            parent = next((q for q in posts if q["id"] == p["reply_to"]), None)
            if parent:
                pa = personas_by_id.get(parent["author"], {}).get("name", parent["author"])
                prefix = f"(↳ {pa}への返信) "
        lines.append(f"[{p['id']}] {author} {p['ts'][11:16]}: {prefix}{p['text']}")
    return "\n".join(lines) if lines else "(まだ投稿がない)"


def main():
    now = datetime.now(JST)
    personas = [load_yaml(f) for f in sorted(glob.glob(str(ROOT / "personas" / "*.yaml")))]
    personas_by_id = {p["id"]: p for p in personas}
    state = load_yaml(ROOT / "world" / "state.yaml")
    rels = load_yaml(ROOT / "world" / "relationships.yaml")
    data = load_posts()
    posts = data["posts"]

    active = pick_active(personas, now.hour)
    if not active:
        print("この時間帯に動く住人はいなかった")
        return

    next_num = max((int(p["id"][1:]) for p in posts), default=0) + 1

    for p in active:
        my_state = state.get(p["id"], [])
        state_text = "\n".join(f"- {s}" for s in my_state)
        timeline = format_timeline(posts, personas_by_id)
        others = "、".join(q["name"] for q in personas if q["id"] != p["id"])

        system = COMMON_RULES + f"""

【あなたの人物】{p['name']}
{p['profile']}
【口調】
{p['tone']}
【口調の例】
""" + "\n".join(f"- {s}" for s in p.get("speech_samples", [])) + f"""
【いまの生活(進行中)】
{state_text}
{p.get('persona_notes', '')}"""

        user = f"""いまは{now.strftime('%m月%d日 %H時%M分')}({['月','火','水','木','金','土','日'][now.weekday()]}曜)。{state.get('season_note','')}
このSNSの住人はあなたを含めて: {p['name']}、{others}。全員が互いの投稿を見ている。

直近のタイムライン:
{timeline}

あなたとの関係:
{relationship_notes(rels, p)}

次のどれかを選んで、JSONだけを出力すること(前置きや```は不要):
- 新規投稿: {{"action":"post","text":"...","likes":["投稿ID",...]}}
- 誰かへの返信(reply_propensity={p['posting']['reply_propensity']}を目安に、自然な流れがある時だけ): {{"action":"reply","reply_to":"投稿ID","text":"...","likes":[]}}
- 今回は呟かず、いいねだけ: {{"action":"skip","likes":["投稿ID",...]}}
likesは心から良いと思った投稿だけ(0〜2件、like_propensity={p['posting']['like_propensity']}が目安)。自分の投稿にはいいねしない。
textは140字以内で自然に終わらせる。タイムラインの誰かと同じ言い回しを繰り返さない。"""

        try:
            result = extract_json(call_claude(system, user))
        except Exception as e:
            print(f"{p['name']}: 生成失敗 ({e})", file=sys.stderr)
            continue

        # いいねを反映
        for liked_id in result.get("likes", [])[:2]:
            for q in posts:
                if q["id"] == liked_id and q["author"] != p["id"]:
                    q.setdefault("likes", [])
                    if p["id"] not in q["likes"]:
                        q["likes"].append(p["id"])

        action = result.get("action")
        if action in ("post", "reply"):
            text = (result.get("text") or "").strip()
            if not text:
                continue
            if len(text) > MAX_LEN:
                text = re.split(r"(?<=[。!?！？])", text)[0][:MAX_LEN]
            reply_to = result.get("reply_to") if action == "reply" else None
            if reply_to and not any(q["id"] == reply_to for q in posts):
                reply_to = None
            posts.append({
                "id": f"p{next_num:05d}",
                "author": p["id"],
                "text": text,
                "ts": now.isoformat(timespec="minutes"),
                "reply_to": reply_to,
                "likes": [],
            })
            next_num += 1
            print(f"{p['name']}: {action} -> {text[:40]}")
        else:
            print(f"{p['name']}: skip (いいねのみ)")

    save_posts(data)


if __name__ == "__main__":
    main()
