"""diagnose_gantt_table_shading.py의 후속. "추진 내용"/"기간" 같은 느슨한
키워드로는 문서 안의 다른 큰 표(예: 목차 전체를 담은 wrapper 표)까지
18개나 걸려서 진짜 "6. 추진일정" 표를 특정하기 어려웠다(2026-08-28,
1차 진단 결과).

이번엔 훨씬 좁게 잡는다: 셀 텍스트가 정확히 "M+1"인 셀이 있는 표만 찾는다
- 이미지 캡처를 보면 헤더 행에 "M", "M+1", ..., "M+6"이 각각 독립된 짧은
셀로 들어가 있으므로, 이 조건이면 목차 같은 큰 텍스트 블록 표는 걸러지고
진짜 Gantt 표만 남을 가능성이 높다.

찾은 표에 대해:
  1. 모든 셀의 text/class/style/rowspan/colspan을 그대로 덤프(1차 진단과 동일)
  2. CSS의 background 관련 규칙을 이번엔 전부(64개 전체) 출력 - 1차
     진단에선 20개까지만 잘라서 출력해 실제 파란 계열 색상이 안 보였을 수
     있다
  3. 각 셀의 class가 어떤 배경색에 매핑되는지 바로 옆에 보여준다(수작업
     대조 없이 바로 판단 가능하게)

[참고] 반드시 한빈 로컬(파이참)에서 실행해야 함 - 공용 개발 환경엔
원본 hwp 파일이 없어 hwp5html 변환 자체가 불가능하다. 1차 진단 스크립트가
이미 output/gantt_diag/에 변환 결과(index.xhtml, styles.css)를 남겨뒀다면
이 스크립트는 그걸 재사용하고, 없으면 새로 변환한다(최대 90초).

파이참에서 우클릭 > Run으로 실행하면 된다(인자 불필요).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR, RAW_FILES_DIR  # noqa: E402

DOC_FILE_NAME = "국방과학연구소_기록관리시스템 통합 활용 및 보안 환경 구축.hwp"
OUT_DIR = OUTPUT_DIR / "gantt_diag"
# "6. 추진일정" 표 헤더 셀에만 나올 법한 짧고 특징적인 텍스트들
_PRECISE_HINTS = ["M+1", "M+2", "M+3"]


def _run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")


def _parse_css(css_files):
    css_rules = {}
    for css_path in css_files:
        css_text = css_path.read_text(encoding="utf-8", errors="ignore")
        for block in css_text.split("}"):
            if "{" not in block:
                continue
            selector, body = block.split("{", 1)
            selector = selector.strip()
            props = {}
            for decl in body.split(";"):
                if ":" in decl:
                    k, v = decl.split(":", 1)
                    props[k.strip()] = v.strip()
            css_rules[selector] = props
    return css_rules


def main():
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("beautifulsoup4가 설치돼 있지 않습니다: pip install beautifulsoup4")
        return

    index_html = OUT_DIR / "index.xhtml"
    if not index_html.exists():
        hwp_path = RAW_FILES_DIR / DOC_FILE_NAME
        if not hwp_path.exists():
            print(f"원본 파일을 찾을 수 없습니다: {hwp_path}")
            return
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        print("hwp5html 변환 중... (최대 90초)")
        try:
            proc = _run(["hwp5html", "--output", str(OUT_DIR), str(hwp_path)], timeout=120)
        except FileNotFoundError:
            print("hwp5html 실행파일을 찾을 수 없습니다 (pyhwp 미설치).")
            return
        except subprocess.TimeoutExpired:
            print("hwp5html 타임아웃(120초).")
            return
        if proc.returncode != 0:
            print("hwp5html 변환 실패:", proc.stderr[:1000])
            return
    else:
        print(f"기존 변환 결과 재사용: {OUT_DIR}")

    css_files = list(OUT_DIR.glob("*.css"))
    css_rules = _parse_css(css_files)
    bg_rules = {sel: props for sel, props in css_rules.items() if any("background" in k for k in props)}
    print(f"\nCSS의 background 관련 규칙 전체 {len(bg_rules)}개:")
    for sel, props in bg_rules.items():
        print(f"  {sel} -> {props.get('background-color', props)}")

    xhtml_text = index_html.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(xhtml_text, "lxml-xml")
    all_tables = soup.find_all("table")

    candidates = []
    for i, table_tag in enumerate(all_tables):
        cell_texts = [c.get_text(strip=True) for c in table_tag.find_all(["td", "th"])]
        if any(hint in cell_texts for hint in _PRECISE_HINTS):
            candidates.append((i, table_tag))

    if not candidates:
        print("\n'M+1'/'M+2'/'M+3'을 셀 텍스트 그대로 가진 표를 못 찾았습니다.")
        print("표 제목 형식이 다를 수 있으니, 1차 진단(diagnose_gantt_table_shading.py)의 18개 후보 중")
        print("각 표의 미리보기(첫 60자)를 다시 확인해서 어느 인덱스가 진짜 추진일정표인지 알려주면")
        print("이 스크립트를 그 인덱스 전용으로 고쳐줄게.")
        return

    print(f"\n'M+1' 등 정확한 헤더 텍스트를 가진 표: {len(candidates)}개 (인덱스 {[i for i, _ in candidates]})")

    for idx, table_tag in candidates:
        print("\n" + "=" * 100)
        print(f"[표 #{idx}] 전체 셀 속성 + 배경색 매핑")
        for r_i, tr in enumerate(table_tag.find_all("tr")):
            cells = tr.find_all(["td", "th"])
            for c_i, cell in enumerate(cells):
                text = cell.get_text(strip=True)
                cls = cell.get("class")
                style = cell.get("style", "")
                bg_from_style = None
                if "background-color" in style:
                    for part in style.split(";"):
                        if "background-color" in part:
                            bg_from_style = part.strip()
                bg_from_class = None
                if cls:
                    cls_key = cls if isinstance(cls, str) else " ".join(cls)
                    rule = css_rules.get(f".{cls_key}") or css_rules.get(cls_key)
                    if rule:
                        bg_from_class = rule.get("background-color")
                print(f"  행{r_i} 셀{c_i} text={text!r:30} class={cls!r} "
                      f"style배경={bg_from_style!r} class배경={bg_from_class!r} "
                      f"colspan={cell.get('colspan')} rowspan={cell.get('rowspan')}")

    print("\n" + "=" * 100)
    print("확인 포인트: 같은 행에서 '색칠된'(캡처상 파란 계열) 칸과 '안 칠해진'(흰색) 칸이")
    print("class배경/style배경 값이 서로 다르게 나오는지 - 다르면 그 값으로 '활성 기간' 여부를 코드로 판별 가능.")


if __name__ == "__main__":
    main()
