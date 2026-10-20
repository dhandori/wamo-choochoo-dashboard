"""한국 종목의 세부 산업 분류표(wamo_industry_map_KR.json)를 만든다.

출처: WAMO 산업 분류표(global-high-radar → industry-strength/config/baskets.json, 제품 단위 분류).
레이더가 쓰던 KRX 광의 업종("sector (broad)")은 2차전지가 화학·지주회사·반도체로 흩어지는 문제가 있어,
이 표에 있는 종목은 세부 산업으로 덮어쓴다. 표에 없는 종목은 기존 분류를 그대로 둔다.

사용법: python wamo_industry_map_build.py /path/to/industry-strength/config/baskets.json
"""
import json, sys
from pathlib import Path

# 분류표에 없는 종목 중 기존 분류가 명백히 틀린 것만 손으로 보정한다.
MANUAL = {
    "007340": ("자동차", "자동차 부품"),          # DN오토모티브: '메모리 반도체'로 잡혀 있던 오류
    "003550": ("지주회사", "지주회사"),            # LG
    "034730": ("지주회사", "지주회사"),            # SK
    "001040": ("지주회사", "지주회사"),            # CJ
    "180640": ("지주회사", "지주회사"),            # 한진칼
    "402340": ("지주회사", "지주회사"),            # SK스퀘어
    "008930": ("지주회사", "지주회사"),            # 한미사이언스
}

def build(baskets_path):
    data = json.loads(Path(baskets_path).read_text(encoding="utf-8"))
    out = {}
    for item in data["markets"]["KR"]["items"]:
        for m in item["members"]:
            row = out.get(m["code"])
            if row is None:
                # 처음 나온 산업이 대표 산업(종목 표에 보이는 이름)이다.
                out[m["code"]] = {"group": item["group"], "industry": item["name"], "name": m.get("name")}
            elif item["name"] != row["industry"]:
                # 겹쳐 소속된 산업(예: ESS 밸류체인). 산업 순위·구성 종목에는 함께 들어간다.
                row.setdefault("also", []).append(item["name"])
    for code, (group, industry) in MANUAL.items():
        out.setdefault(code, {"group": group, "industry": industry})
    return {"_source": "WAMO 산업 분류표(global-high-radar 제품 단위) + 수동 보정", "map": dict(sorted(out.items()))}

if __name__ == "__main__":
    result = build(sys.argv[1])
    Path(__file__).resolve().parent.joinpath("wamo_industry_map_KR.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("종목", len(result["map"]), "개 기록")
