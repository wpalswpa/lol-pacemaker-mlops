"""Data Dragon(Riot 공개 정적 자료)에서 api/game_data.json 을 만든다. 패치마다 사람이 돌린다.

- 챔피언: 영문 ID → 한국어 이름, tags, info(attack·magic)
- 아이템: 규칙표(api/build_rules.json)가 쓰는 ID 만 → 한국어 이름, 가격. 그 판에 없거나 소환사의 협곡 아이템이 아니면 실패
키가 필요 없다. 네트워크가 있을 때만 돈다(서빙·테스트는 만들어 둔 game_data.json 만 읽는다).
  uv run python -m training.ddragon            최신 판
  uv run python -m training.ddragon 16.23.1    판 지정
"""
import json
import sys
import urllib.request
from typing import Any

BASE = "https://ddragon.leagueoflegends.com"
RULES = "api/build_rules.json"
OUT = "api/game_data.json"


def get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def rule_item_ids(rules: dict) -> set[int]:
    """규칙표 안의 모든 아이템 ID (중첩 표를 다 훑는다)."""
    keys = ("boots", "anti_heal", "anti_tank", "defensive_component", "damage_component", "core", "starter")
    ids: set[int] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, int):
            ids.add(v)

    for k in keys:
        walk(rules[k])
    return ids


def main(argv: list[str]) -> int:
    version = argv[0] if argv else get(f"{BASE}/api/versions.json")[0]
    champs = get(f"{BASE}/cdn/{version}/data/ko_KR/champion.json")["data"]
    items = get(f"{BASE}/cdn/{version}/data/ko_KR/item.json")["data"]
    rules = json.load(open(RULES, encoding="utf-8"))

    bad = []
    out_items = {}
    for i in sorted(rule_item_ids(rules)):
        d = items.get(str(i))
        if not d or not d.get("maps", {}).get("11"):
            bad.append(i)
            continue
        out_items[str(i)] = {"name": d["name"], "gold": d["gold"]["total"]}
    missing_skill = [c for c in rules["skill_orders"] if c not in champs]
    missing_heal = [c for c in rules["heal_champions"] if c not in champs]
    if bad or missing_skill or missing_heal:
        print(f"Data Dragon {version} 에 없는 것: 아이템 {bad}, 스킬표 챔피언 {missing_skill}, 치유 챔피언 {missing_heal}",
              file=sys.stderr)
        return 1

    data = {
        "source": f"Data Dragon {version} (ko_KR)",
        "version": version,
        "champions": {cid: {"name": c["name"], "tags": c["tags"],
                            "attack": c["info"]["attack"], "magic": c["info"]["magic"]}
                      for cid, c in sorted(champs.items())},
        "items": out_items,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"{OUT}: Data Dragon {version}, 챔피언 {len(data['champions'])}개, 규칙표 아이템 {len(out_items)}개 모두 확인")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
