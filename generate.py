#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NFL 観戦優先度ガイド ジェネレーター
============================================
ESPN の公開データ(APIキー不要・無料)から NFL の試合を取得し、
- プレイオフ影響度 / 地区順位影響度 / 得点 / 接戦度 / 逆転数
の5要素を重み付け合成して ⭐️1〜5 を算出。
勝者・最終スコアは一切表示せず(=ネタバレ防止)、
「接戦」「撃ち合い」等のネタバレにならない特徴のみをタグ/レビューで見せる。

- 進行中シーズンをトップ、過去シーズンは折りたたみ
- preseason各week / regular各week / wildcard / divisional / conference / superbowl で分割
- 49ers(SF)の試合は評価に関わらず各セクション最上段、それ以外は⭐️(スコア)順

出力: index.html(単一ファイル・自己完結)
キャッシュ: data/games_<year>.json(確定した試合は再取得しない)
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import random
import sys
import urllib.request
import urllib.error

BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
OUT_HTML = os.path.join(HERE, "index.html")
UA = {"User-Agent": "curl/8.4.0"}  # ESPN の WAF は素っ気ない/一部の UA を弾くため curl 相当を使用

PIN_TEAM = "SF"  # 49ers を最上段固定

# NFL 地区マップ(ESPN 略称ベース)。地区対決判定に使用。
DIVISIONS = {
    "BUF": ("AFC", "East"), "MIA": ("AFC", "East"), "NE": ("AFC", "East"), "NYJ": ("AFC", "East"),
    "BAL": ("AFC", "North"), "CIN": ("AFC", "North"), "CLE": ("AFC", "North"), "PIT": ("AFC", "North"),
    "HOU": ("AFC", "South"), "IND": ("AFC", "South"), "JAX": ("AFC", "South"), "TEN": ("AFC", "South"),
    "DEN": ("AFC", "West"), "KC": ("AFC", "West"), "LV": ("AFC", "West"), "LAC": ("AFC", "West"),
    "DAL": ("NFC", "East"), "NYG": ("NFC", "East"), "PHI": ("NFC", "East"), "WSH": ("NFC", "East"),
    "CHI": ("NFC", "North"), "DET": ("NFC", "North"), "GB": ("NFC", "North"), "MIN": ("NFC", "North"),
    "ATL": ("NFC", "South"), "CAR": ("NFC", "South"), "NO": ("NFC", "South"), "TB": ("NFC", "South"),
    "ARI": ("NFC", "West"), "LAR": ("NFC", "West"), "SF": ("NFC", "West"), "SEA": ("NFC", "West"),
}

# ⭐️ 合成の重み(調整可能)
WEIGHTS = {
    "excitement": 0.55,   # 接戦度・リード変動・逆転(watchability)
    "importance": 0.30,   # プレイオフ/地区順位への影響度(stakes)
    "scoring": 0.15,      # 得点(点の多さ)
}
# excitement 内部の内訳
EXC_WEIGHTS = {"tension": 0.35, "swing": 0.30, "leadchg": 0.20, "comeback": 0.15}

POST_ROUNDS = {1: "wildcard", 2: "divisional", 3: "conference", 5: "superbowl"}
ROUND_LABEL = {
    "wildcard": "ワイルドカード", "divisional": "ディビジョナル",
    "conference": "カンファレンス", "superbowl": "スーパーボウル",
}


# ---------------------------------------------------------------------------
# 取得
# ---------------------------------------------------------------------------
def fetch_json(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def current_pointer():
    """現在のシーズン年・種別・週を返す。週は「進行中の週」を指す。"""
    d = fetch_json(BASE + "/scoreboard")
    s = d["leagues"][0]["season"]
    wk = int((d.get("week") or {}).get("number") or 0)
    return int(s["year"]), int(s["type"]["type"]), wk


def scoreboard_events(year, seasontype, week):
    url = f"{BASE}/scoreboard?dates={year}&seasontype={seasontype}&week={week}"
    try:
        d = fetch_json(url)
    except Exception:
        return []
    return d.get("events", [])


def game_summary(event_id):
    return fetch_json(f"{BASE}/summary?event={event_id}")


# ---------------------------------------------------------------------------
# 指標の算出
# ---------------------------------------------------------------------------
def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def analyze(event, summary, seasontype, week):
    """1試合の指標・⭐️・レビューを算出。勝者/スコアは戻り値に含めるが表示はしない。"""
    comp = summary.get("header", {}).get("competitions", [{}])[0]
    competitors = comp.get("competitors", [])
    home = away = None
    for c in competitors:
        team = c.get("team", {})
        info = {
            "abbr": team.get("abbreviation", "?"),
            "name": team.get("shortDisplayName") or team.get("displayName") or "",
            "logo": f"https://a.espncdn.com/i/teamlogos/nfl/500/{(team.get('abbreviation','') or '').lower()}.png",
            "score": int(c.get("score") or 0),
        }
        if c.get("homeAway") == "home":
            home = info
        else:
            away = info
    if not home or not away:
        return None

    total_points = home["score"] + away["score"]
    margin = abs(home["score"] - away["score"])

    # --- scoring plays からスコアのリード変遷 ---
    score_lead_changes = 0
    prev_sign = 0
    for sp in summary.get("scoringPlays", []):
        diff = int(sp.get("homeScore", 0)) - int(sp.get("awayScore", 0))
        sign = (diff > 0) - (diff < 0)
        if sign != 0 and prev_sign != 0 and sign != prev_sign:
            score_lead_changes += 1
        if sign != 0:
            prev_sign = sign

    # --- win probability(あれば)---
    wp = [e.get("homeWinPercentage") for e in summary.get("winprobability", [])
          if e.get("homeWinPercentage") is not None]
    has_wp = len(wp) >= 5

    if has_wp:
        # 勝者視点の勝率系列
        home_won = home["score"] > away["score"]
        win_series = wp if home_won else [1 - x for x in wp]
        # 総変動量(excitement index)
        swing_total = sum(abs(wp[i] - wp[i - 1]) for i in range(1, len(wp)))
        swing_norm = clamp(swing_total / 8.0)
        # 勝率50%を跨いだ回数
        wp_lead_changes = 0
        for i in range(1, len(wp)):
            if (wp[i] - 0.5) * (wp[i - 1] - 0.5) < 0:
                wp_lead_changes += 1
        # 緊張度: 勝率が15%〜85%に収まっていた割合
        tension = sum(1 for x in wp if 0.15 < x < 0.85) / len(wp)
        # 逆転度: 勝者が最も追い込まれた時の勝率(低いほど大逆転)
        min_winner_wp = min(win_series)
        comeback = clamp((0.5 - min_winner_wp) / 0.4) if min_winner_wp < 0.5 else 0.0
        leadchg_norm = clamp(wp_lead_changes / 6.0)
    else:
        # フォールバック(preseason 等 WP 無し): スコア主体で近似
        swing_norm = clamp(score_lead_changes / 5.0)
        tension = clamp(1.0 - margin / 21.0)
        comeback = clamp((score_lead_changes - 1) / 4.0) if score_lead_changes >= 2 else 0.0
        leadchg_norm = clamp(score_lead_changes / 4.0)
        wp_lead_changes = score_lead_changes

    scoring_norm = clamp((total_points - 24) / 44.0)

    excitement = (EXC_WEIGHTS["tension"] * tension
                  + EXC_WEIGHTS["swing"] * swing_norm
                  + EXC_WEIGHTS["leadchg"] * leadchg_norm
                  + EXC_WEIGHTS["comeback"] * comeback)

    # --- 重要度(プレイオフ影響度 + 地区順位影響度)---
    same_div = (DIVISIONS.get(home["abbr"], (None, None))
                == DIVISIONS.get(away["abbr"], (None, "x"))
                and home["abbr"] in DIVISIONS)
    if seasontype == 3:  # postseason
        round_key = POST_ROUNDS.get(week, "wildcard")
        importance = {"wildcard": 0.90, "divisional": 0.95,
                      "conference": 0.98, "superbowl": 1.0}[round_key]
    elif seasontype == 2:  # regular
        # 週の後半ほどプレイオフ影響大
        base = 0.45 + 0.40 * clamp((week - 1) / 17.0)
        if same_div:
            base += 0.15  # 地区順位への影響
        importance = clamp(base)
    else:  # preseason
        importance = 0.18

    overall = (WEIGHTS["excitement"] * excitement
               + WEIGHTS["importance"] * importance
               + WEIGHTS["scoring"] * scoring_norm)
    overall = clamp(overall)
    stars = int(round(1 + overall * 4))
    stars = max(1, min(5, stars))

    tags = build_tags(seasontype, week, same_div, tension, swing_norm,
                      wp_lead_changes, comeback, scoring_norm, total_points,
                      home["abbr"], away["abbr"])
    review = build_review(event, seasontype, week, same_div, tension, swing_norm,
                          wp_lead_changes, comeback, scoring_norm, total_points)

    return {
        "id": event["id"],
        "date": event.get("date"),
        "home": {k: home[k] for k in ("abbr", "name", "logo")},
        "away": {k: away[k] for k in ("abbr", "name", "logo")},
        "stars": stars,
        "score": round(overall, 4),  # 並び替え用の連続値
        "tags": tags,
        "review": review,
        "pinned": home["abbr"] == PIN_TEAM or away["abbr"] == PIN_TEAM,
        "final": event.get("status", {}).get("type", {}).get("name") == "STATUS_FINAL",
        # metrics(参考・非表示のものも保持)
        "_m": {"tension": round(tension, 3), "swing": round(swing_norm, 3),
               "leadchg": wp_lead_changes, "comeback": round(comeback, 3),
               "points": total_points, "importance": round(importance, 3)},
    }


# ---------------------------------------------------------------------------
# タグ / レビュー(ネタバレなし・映画評風)
# ---------------------------------------------------------------------------
def build_tags(seasontype, week, same_div, tension, swing, leadchg,
               comeback, scoring_norm, total_points, home_abbr, away_abbr):
    tags = []
    if home_abbr == PIN_TEAM or away_abbr == PIN_TEAM:
        tags.append("49ers")
    if seasontype == 3:
        tags.append(ROUND_LABEL[POST_ROUNDS.get(week, "wildcard")])
    elif seasontype == 2:
        if same_div:
            tags.append("地区対決")
        if week >= 15:
            tags.append("プレイオフ争い")
    else:
        tags.append("プレシーズン")
    if tension >= 0.6 or swing >= 0.8:
        tags.append("大接戦")
    elif tension >= 0.4:
        tags.append("接戦")
    if leadchg >= 4:
        tags.append("シーソー")
    if comeback >= 0.5:
        tags.append("劇的な巻き返し")
    if total_points >= 55:
        tags.append("撃ち合い")
    elif total_points <= 30:
        tags.append("守備戦")
    return tags


def build_review(event, seasontype, week, same_div, tension, swing, leadchg,
                 comeback, scoring_norm, total_points):
    rnd = random.Random(event["id"])  # 試合ごとに決定的(毎回同じ文)

    # ステークス前置き
    if seasontype == 3:
        rk = POST_ROUNDS.get(week, "wildcard")
        stakes = {
            "wildcard": ["負けられないポストシーズン初戦。", "一発勝負のワイルドカード。"],
            "divisional": ["勝てば残り2、負ければ終わりの一戦。", "頂点が見えてくるディビジョナル。"],
            "conference": ["カンファレンス制覇をかけた大一番。", "スーパーボウルへの最後の関門。"],
            "superbowl": ["シーズンの頂点を決める頂上決戦。", "全てがかかった最終決戦。"],
        }[rk]
    elif seasontype == 2:
        if week >= 15:
            stakes = ["プレイオフ争いに直結する終盤戦。", "順位に大きく響く重要局面。"]
        elif same_div:
            stakes = ["地区の順位を左右するライバル対決。", "意地がぶつかる地区対決。"]
        else:
            stakes = ["レギュラーシーズンの一戦。", "各チームの現在地が見える一戦。"]
    else:
        stakes = ["本番前の調整ゲーム。", "若手の見どころ中心のプレシーズン。"]

    # テンション本文
    if tension >= 0.6 or swing >= 0.85:
        body = ["最後の1プレーまで先が読めない、心臓に悪い展開。",
                "終始どちらに転んでもおかしくない緊張感が続く。",
                "手に汗握る攻防が最後まで途切れない。"]
    elif tension >= 0.4 or swing >= 0.6:
        body = ["終盤までもつれ込み、目が離せない。",
                "一進一退でじわじわ盛り上がる展開。",
                "要所に山場があり見応え十分。"]
    elif tension >= 0.2:
        body = ["中盤に見どころのある手堅い試合。",
                "落ち着いて観られる、ほどよい緊張感。"]
    else:
        body = ["流れが読みやすく、気楽に流し観できる一戦。",
                "波乱は少なめ、結果を気にせず楽しめる。"]

    parts = [rnd.choice(stakes), rnd.choice(body)]

    # スパイス(重複しすぎないよう最大1つ)
    spice = []
    if comeback >= 0.5:
        spice.append("劣勢からの巻き返しの気配あり。")
    if leadchg >= 4:
        spice.append("リードが何度も入れ替わるシーソーゲーム。")
    if total_points >= 55:
        spice.append("攻撃陣が躍動する点の取り合い。")
    elif total_points <= 30 and seasontype != 1:
        spice.append("締まった守備戦、ロースコアの妙。")
    if spice:
        parts.append(rnd.choice(spice))

    return "".join(parts)


# ---------------------------------------------------------------------------
# シーズン収集(キャッシュ利用)
# ---------------------------------------------------------------------------
def cache_path(year):
    return os.path.join(DATA_DIR, f"games_{year}.json")


def load_cache(year):
    p = cache_path(year)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(year, cache):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(cache_path(year), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def phase_weeks(seasontype):
    if seasontype == 1:
        return list(range(1, 5))          # preseason wk1-4
    if seasontype == 2:
        return list(range(1, 19))         # regular wk1-18
    return [1, 2, 3, 5]                    # postseason(4=Pro Bowl は除外)


def collect_season(year, max_workers=8, verbose=True, live=None):
    """1シーズン分を集めて {phase: {...}} を返す。確定済みはキャッシュから。

    live=(cur_year, cur_type, cur_week): ESPN が指す「進行中の週」。
    現在シーズンの現在フェーズでは、この進行中の週(および以降)は
    たとえ FINAL でも“まだ完了扱いにしない”ため除外する。
    (データ提供元が進行中の週の試合を早々に FINAL 化するケースへの対策)
    """
    cache = load_cache(year)  # id -> analyzed dict
    # 収集対象イベント: (seasontype, week, event)
    jobs = []
    for st in (1, 2, 3):
        for wk in phase_weeks(st):
            # 進行中の週は除外(現在シーズン・現在フェーズのみ)
            if live and year == live[0] and st == live[1] and live[2] and wk >= live[2]:
                continue
            for ev in scoreboard_events(year, st, wk):
                status = ev.get("status", {}).get("type", {}).get("name")
                if status != "STATUS_FINAL":
                    continue  # 確定した試合のみ(レビュー可能)
                jobs.append((st, wk, ev))

    to_fetch = [(st, wk, ev) for (st, wk, ev) in jobs if ev["id"] not in cache]
    if verbose:
        print(f"[{year}] final games: {len(jobs)}  (cached: {len(jobs)-len(to_fetch)}, "
              f"fetching: {len(to_fetch)})", file=sys.stderr)

    def work(item):
        st, wk, ev = item
        try:
            s = game_summary(ev["id"])
            res = analyze(ev, s, st, wk)
            return ev["id"], res
        except Exception as e:
            if verbose:
                print(f"  ! {ev.get('name','?')} 取得失敗: {e}", file=sys.stderr)
            return ev["id"], None

    if to_fetch:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for eid, res in ex.map(work, to_fetch):
                if res:
                    cache[eid] = res
        save_cache(year, cache)

    # phase 構造へ整理
    id_to_meta = {ev["id"]: (st, wk) for (st, wk, ev) in jobs}
    phases = {"pre": {}, "reg": {}, "post": {}}
    for eid, g in cache.items():
        if eid not in id_to_meta:
            continue  # 今回対象外(別シーズン等)
        st, wk = id_to_meta[eid]
        if st == 1:
            phases["pre"].setdefault(str(wk), []).append(g)
        elif st == 2:
            phases["reg"].setdefault(str(wk), []).append(g)
        else:
            phases["post"].setdefault(POST_ROUNDS.get(wk, "wildcard"), []).append(g)

    # 並び替え: 49ers 最上段固定 → スコア降順
    def sort_games(lst):
        lst.sort(key=lambda g: (not g["pinned"], -g["score"]))
        return lst

    for wk in phases["pre"]:
        sort_games(phases["pre"][wk])
    for wk in phases["reg"]:
        sort_games(phases["reg"][wk])
    for rk in phases["post"]:
        sort_games(phases["post"][rk])

    return phases


# ---------------------------------------------------------------------------
# HTML 生成
# ---------------------------------------------------------------------------
def build_data(seasons, current_year, current_type, live=None):
    out = {"generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
           "currentSeason": current_year, "currentType": current_type,
           "seasons": []}
    for year in seasons:
        phases = collect_season(year, live=live)
        out["seasons"].append({"year": year, "phases": phases})
    return out


def render_html(data):
    payload = json.dumps(data, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA__", payload)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NFL 観戦ガイド</title>
<style>
:root{
  --bg:#0b0f1a; --panel:#131a2b; --panel2:#0f1524; --line:#24304a;
  --txt:#e7ecf5; --muted:#93a0b8; --accent:#3ea6ff; --gold:#ffcf4d;
  --pin:#d4302f; --chip:#1d2740; --chip-txt:#b9c6e0;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
  font-family:-apple-system,"Segoe UI",Roboto,"Helvetica Neue","Noto Sans JP",sans-serif;
  line-height:1.6;-webkit-font-smoothing:antialiased}
header{padding:22px 18px 10px;border-bottom:1px solid var(--line);
  background:linear-gradient(180deg,#0e1524,#0b0f1a)}
h1{margin:0;font-size:1.35rem;letter-spacing:.02em}
.sub{color:var(--muted);font-size:.82rem;margin-top:4px}
.wrap{max-width:1080px;margin:0 auto;padding:0 14px 60px}
.controls{position:sticky;top:0;z-index:5;background:rgba(11,15,26,.94);
  backdrop-filter:blur(8px);padding:12px 0 8px;border-bottom:1px solid var(--line)}
.row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:5px 0}
.row .lbl{color:var(--muted);font-size:.72rem;width:56px;flex:none}
button.seg{background:var(--chip);color:var(--chip-txt);border:1px solid var(--line);
  border-radius:999px;padding:5px 12px;font-size:.8rem;cursor:pointer;transition:.15s}
button.seg:hover{border-color:var(--accent)}
button.seg.on{background:var(--accent);color:#04121f;border-color:var(--accent);font-weight:700}
button.seg.pin{border-color:var(--pin)}
.season-past{color:var(--muted);font-size:.72rem;margin:14px 0 2px;
  text-transform:uppercase;letter-spacing:.12em}
.cards{display:grid;grid-template-columns:1fr;gap:10px;margin-top:14px}
@media(min-width:720px){.cards{grid-template-columns:1fr 1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:14px 15px;display:flex;flex-direction:column;gap:9px;position:relative;overflow:hidden}
.card.pinned{border-color:var(--pin);box-shadow:0 0 0 1px rgba(212,48,47,.35) inset}
.pinflag{position:absolute;top:0;right:0;background:var(--pin);color:#fff;
  font-size:.62rem;font-weight:700;padding:3px 9px;border-bottom-left-radius:10px;letter-spacing:.05em}
.match{display:flex;align-items:center;gap:10px}
.team{display:flex;align-items:center;gap:7px;min-width:0}
.team img{width:30px;height:30px;object-fit:contain;flex:none}
.team .ab{font-weight:700;font-size:.98rem}
.vs{color:var(--muted);font-size:.72rem;padding:0 2px}
.date{color:var(--muted);font-size:.72rem;margin-left:auto;text-align:right}
.stars{font-size:1.02rem;letter-spacing:1px;color:var(--gold)}
.stars .off{color:#39435c}
.review{font-size:.86rem;color:#d6deec}
.tags{display:flex;flex-wrap:wrap;gap:5px}
.tag{background:var(--chip);color:var(--chip-txt);border:1px solid var(--line);
  border-radius:6px;padding:2px 8px;font-size:.68rem}
.tag.t49{background:rgba(212,48,47,.16);color:#ff9b9a;border-color:rgba(212,48,47,.5)}
.tag.tplay{background:rgba(255,207,77,.14);color:#ffdd80;border-color:rgba(255,207,77,.4)}
.empty{color:var(--muted);text-align:center;padding:40px 0;font-size:.9rem}
.foot{color:var(--muted);font-size:.72rem;margin-top:26px;border-top:1px solid var(--line);padding-top:12px}
.legend{color:var(--muted);font-size:.72rem;margin-top:6px}
</style>
</head>
<body>
<header>
  <div class="wrap" style="padding-bottom:0">
    <h1>🏈 NFL 観戦ガイド</h1>
    <div class="sub" id="subtitle"></div>
  </div>
</header>
<div class="wrap">
  <div class="controls">
    <div class="row"><span class="lbl">シーズン</span><span id="seasonBtns"></span></div>
    <div class="row"><span class="lbl">フェーズ</span><span id="phaseBtns"></span></div>
    <div class="row"><span class="lbl">週/ラウンド</span><span id="weekBtns"></span></div>
  </div>
  <div class="legend">⭐️=総合おすすめ度(プレイオフ影響度・地区順位・得点・接戦度・逆転を合成)。
    <span style="color:#ff9b9a">赤枠</span>=49ers は評価に関わらず最上段固定。勝敗・スコアは表示していません(ネタバレ防止)。</div>
  <div class="cards" id="cards"></div>
  <div class="foot" id="foot"></div>
</div>

<script>
const DATA = __DATA__;
const PHASE_LABEL = {pre:"プレシーズン", reg:"レギュラー", post:"ポストシーズン"};
const ROUND_LABEL = {wildcard:"ワイルドカード", divisional:"ディビジョナル",
  conference:"カンファレンス", superbowl:"スーパーボウル"};
const ROUND_ORDER = ["wildcard","divisional","conference","superbowl"];
const PLAY_TAGS = new Set(["ワイルドカード","ディビジョナル","カンファレンス","スーパーボウル","プレイオフ争い"]);

const state = {season:null, phase:null, week:null};

function seasonObj(y){ return DATA.seasons.find(s=>s.year===y); }

function availablePhases(y){
  const s=seasonObj(y); const out=[];
  if(s){
    if(Object.keys(s.phases.pre||{}).length) out.push("pre");
    if(Object.keys(s.phases.reg||{}).length) out.push("reg");
    if(Object.keys(s.phases.post||{}).length) out.push("post");
  }
  return out;
}
function availableWeeks(y,phase){
  const s=seasonObj(y); if(!s) return [];
  if(phase==="post"){
    return ROUND_ORDER.filter(r=>(s.phases.post||{})[r]);
  }
  return Object.keys(s.phases[phase]||{}).map(Number).sort((a,b)=>a-b).map(String);
}
function gamesFor(y,phase,week){
  const s=seasonObj(y); if(!s) return [];
  return (s.phases[phase]||{})[week] || [];
}

function starStr(n){
  let s="";
  for(let i=0;i<5;i++) s+= i<n ? "★" : "<span class='off'>★</span>";
  return s;
}
function fmtDate(iso){
  if(!iso) return "";
  const d=new Date(iso);
  return (d.getMonth()+1)+"/"+d.getDate();
}

function render(){
  // subtitle
  const gen=new Date(DATA.generatedAt);
  document.getElementById("subtitle").textContent =
    "最終更新: "+gen.toLocaleString("ja-JP",{timeZone:"Asia/Tokyo"})+" (JST) ・ 更新: 土日月火 15:00";

  // season buttons: current first, past grouped
  const cur=DATA.currentSeason;
  const years=DATA.seasons.map(s=>s.year);
  const past=years.filter(y=>y!==cur).sort((a,b)=>b-a);
  const sb=document.getElementById("seasonBtns"); sb.innerHTML="";
  const mk=(y)=>{const b=document.createElement("button");b.className="seg"+(y===state.season?" on":"");
    b.textContent=y+(y===cur?" ★今季":"");b.onclick=()=>{selectSeason(y)};return b;};
  if(years.includes(cur)) sb.appendChild(mk(cur));
  if(past.length){
    const lab=document.createElement("span");lab.className="season-past";lab.textContent="過去のシーズン ▾";
    sb.appendChild(document.createElement("br"));sb.appendChild(lab);sb.appendChild(document.createElement("br"));
    past.forEach(y=>sb.appendChild(mk(y)));
  }

  // phase buttons
  const pb=document.getElementById("phaseBtns"); pb.innerHTML="";
  availablePhases(state.season).forEach(p=>{
    const b=document.createElement("button");b.className="seg"+(p===state.phase?" on":"");
    b.textContent=PHASE_LABEL[p];b.onclick=()=>{state.phase=p;fixWeek();render()};pb.appendChild(b);
  });

  // week buttons
  const wb=document.getElementById("weekBtns"); wb.innerHTML="";
  availableWeeks(state.season,state.phase).forEach(w=>{
    const b=document.createElement("button");b.className="seg"+(w===state.week?" on":"");
    b.textContent = state.phase==="post" ? ROUND_LABEL[w] : ("Week "+w);
    b.onclick=()=>{state.week=w;render()};wb.appendChild(b);
  });

  // cards
  const cont=document.getElementById("cards"); cont.innerHTML="";
  const games=gamesFor(state.season,state.phase,state.week);
  if(!games.length){
    cont.innerHTML='<div class="empty">この週の確定した試合はまだありません。</div>';
  } else {
    games.forEach(g=>cont.appendChild(cardEl(g)));
  }
  document.getElementById("foot").textContent =
    "データ: ESPN 公開データより算出 ・ 試合数 "+games.length+" 件表示中。";
}

function cardEl(g){
  const c=document.createElement("div");
  c.className="card"+(g.pinned?" pinned":"");
  if(g.pinned){const f=document.createElement("div");f.className="pinflag";f.textContent="49ers";c.appendChild(f);}
  const match=document.createElement("div");match.className="match";
  match.innerHTML =
    '<div class="team"><img src="'+g.away.logo+'" onerror="this.style.display=\'none\'"><span class="ab">'+g.away.abbr+'</span></div>'+
    '<span class="vs">@</span>'+
    '<div class="team"><img src="'+g.home.logo+'" onerror="this.style.display=\'none\'"><span class="ab">'+g.home.abbr+'</span></div>'+
    '<span class="date">'+fmtDate(g.date)+'</span>';
  c.appendChild(match);
  const st=document.createElement("div");st.className="stars";st.innerHTML=starStr(g.stars);c.appendChild(st);
  const rv=document.createElement("div");rv.className="review";rv.textContent=g.review;c.appendChild(rv);
  if(g.tags&&g.tags.length){
    const t=document.createElement("div");t.className="tags";
    g.tags.forEach(tag=>{
      const s=document.createElement("span");
      s.className="tag"+(tag==="49ers"?" t49":"")+(PLAY_TAGS.has(tag)?" tplay":"");
      s.textContent=tag;t.appendChild(s);
    });
    c.appendChild(t);
  }
  return c;
}

function fixWeek(){
  const ws=availableWeeks(state.season,state.phase);
  if(!ws.includes(state.week)) state.week = ws.length ? ws[ws.length-1] : null;
}
function selectSeason(y){
  state.season=y;
  const ph=availablePhases(y);
  // 今季なら最新フェーズ、それ以外はポスト>レギュラー>プレの優先
  state.phase = ph.length ? ph[ph.length-1] : null;
  fixWeek();
  render();
}

// 初期表示: 今季の最新週
(function init(){
  const cur=DATA.currentSeason;
  const start = DATA.seasons.find(s=>s.year===cur) ? cur : (DATA.seasons[0]||{}).year;
  selectSeason(start);
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="", help="対象シーズン(カンマ区切り)。未指定なら 今季と前季")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    cur_year, cur_type, cur_week = current_pointer()
    if args.seasons.strip():
        seasons = [int(x) for x in args.seasons.split(",") if x.strip()]
    else:
        seasons = [cur_year, cur_year - 1]
    print(f"current: season={cur_year} type={cur_type} week={cur_week} / targets={seasons}",
          file=sys.stderr)

    data = build_data(seasons, cur_year, cur_type, live=(cur_year, cur_type, cur_week))
    html = render_html(data)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {OUT_HTML}", file=sys.stderr)


if __name__ == "__main__":
    main()
