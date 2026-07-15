#!/usr/bin/env python3
"""週1回、各住人の「進行中の生活」を少しだけ進める。"""
import json, os, re, glob
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
import requests

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.environ.get("TOMARIGI_MODEL", "claude-haiku-4-5-20251001")
STATE_PATH = ROOT / "world" / "state.yaml"


def main():
    now = datetime.now(JST)
    state = yaml.safe_load(STATE_PATH.read_text(encoding="utf-8"))
    posts = json.loads((ROOT / "docs" / "posts.json").read_text(encoding="utf-8"))["posts"]
    personas = {p["id"]: p for p in
                (yaml.safe_load(open(f, encoding="utf-8")) for f in glob.glob(str(ROOT / "personas" / "*.yaml")))}

    week_ago = (now - timedelta(days=7)).isoformat()
    recent = [p for p in posts if p["ts"] >= week_ago]
    recent_text = "\n".join(
        f"{personas.get(p['author'], {}).get('name', p['author'])}: {p['text']}" for p in recent
    ) or "(今週の投稿なし)"

    system = """あなたは小さな架空SNSの世界の進行係です。各住人の「進行中の生活」リストを、
一週間ぶんだけ自然に前進させてください。掟:
- 劇的な事件を起こさない。生活は少しずつしか動かない。
- 解決したことは消してよい。新しい小さな出来事を1人につき最大1つ足してよい。
- 各住人3項目前後を保つ。文体は簡潔な体言止め〜短文。
- season_noteは今の時期に合わせて更新する。
- 出力は入力と同じ構造のYAMLのみ。コードフェンスは不要。"""

    user = f"""今日は{now.strftime('%Y年%m月%d日')}。
現在のstate.yaml:
---
{yaml.safe_dump(state, allow_unicode=True, sort_keys=False)}
---
今週の投稿(参考。投稿で起きたことと矛盾させない):
{recent_text}"""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 2000, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=120,
    )
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
    text = re.sub(r"^```(?:yaml)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    new_state = yaml.safe_load(text)  # パースできなければ例外で落ちて、旧stateが残る
    assert isinstance(new_state, dict) and "season_note" in new_state
    STATE_PATH.write_text(yaml.safe_dump(new_state, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print("state.yaml を更新した")


if __name__ == "__main__":
    main()
