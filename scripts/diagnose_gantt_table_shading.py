"""추진일정표(Gantt형 표) 같은, 셀 "색칠"로 의미가 표현되는 표를 실제로 어떻게
파싱할 수 있을지 알아보기 위한 진단 스크립트.

[2026-08-28] 한빈이 보내준 "6. 추진일정" 표 캡처를 보면, 각 작업(행)이
어느 기간(M/M+1/.../M+6, 열)에 해당하는지가 셀 안의 글자가 아니라 셀
배경색(파란 계열로 칠해짐)으로 표현돼 있다. 지금 파이프라인
(hwp_parser.extract_tables -> merge_text._flatten_tables)은 각 셀에서
텍스트만 뽑는다(`c.get_text(strip=True)`) - 색칠된 셀 안에 문단기호(↵)
말고 실제 글자가 없으면 빈 문자열이 되고, merge_text._flatten_tables()의
"셀이 전부 빈 문자열이면(추진일정표의 문단기호만 있는 빈 칸 등) line이
빈 문자열이 돼서 건너뛴다"는 이미 예상돼 있던 처리 때문에 이런 표는
사실상 통째로 사라진다. 즉 "6. 추진일정" 같은 표는 지금 구조로는 표
내용이 있어도(n_tables엔 잡힘) 실제 일정 정보는 본문 텍스트 어디에도
안 남는다.

이 스크립트는 코드를 고치기 전에, 실제로 hwp5html이 이 표를 어떤 HTML
구조로 뱉는지부터 눈으로 확인하기 위한 것:
  - hwp5html로 변환한 XHTML을 (임시폴더가 아니라) output/gantt_diag/ 에
    그대로 남겨서 직접 열어볼 수 있게 하고
  - "추진 내용"/"M+1" 같은 문구가 들어간 표를 자동으로 찾아서
  - 그 표의 모든 셀에 대해 태그/텍스트/style/class/bgcolor 속성을 전부 출력
  - class 속성이 쓰였다면 같이 생성된 CSS 파일에서 그 class의 배경색
    규칙도 찾아서 같이 보여준다

이 정보를 보면 "셀이 칠해졌는지"를 코드로 판별할 방법(style의
background-color 값 파싱 / class -> CSS 매핑 / bgcolor 속성 등)을 알 수
있고, 그걸 기반으로 hwp_parser.extract_tables()나 merge_text._flatten_tables()
를 어떻게 고쳐야 할지 설계할 수 있다.

[참고] 반드시 한빈 로컬(파이참)에서 실행해야 함 - 공용 개발 환경엔
이 문서의 원본 hwp 파일 자체가 없어서(data/files/ 비어있음) hwp5html
변환을 시도할 수조차 없다.

파이참에서 우클릭 > Run으로 실행하면 된다(인자 불필요).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR, RAW_FILES_DIR  # noqa: E402

DOC_FILE_NAME = "국방과학연구소_기록관리시스템 통합 활용 및 보안 환경 구축.hwp"
OUT_DIR = OUTPUT_DIR / "gantt_diag"
# 표를 찾을 때 쓸 단서 키워드(이미지에 보이는 헤더 문구들)
_TABLE_HINT_KEYWORDS = ["추진 내용", "추진내용", "M+1", "기       간", "기간"]


def _run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")


def main():
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("beautifulsoup4가 설치돼 있지 않습니다: pip install beautifulsoup4")
        return

    hwp_path = RAW_FILES_DIR / DOC_FILE_NAME
    if not hwp_path.exists():
        print(f"원본 파일을 찾을 수 없습니다: {hwp_path}")
        print("data/files/ 폴더에 이 hwp 파일이 있는지 확인해주세요.")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"hwp5html 변환 중... (문서 크기에 따라 최대 90초 정도 걸릴 수 있음)")
    try:
        proc = _run(["hwp5html", "--output", str(OUT_DIR), str(hwp_path)], timeout=120)
    except FileNotFoundError:
        print("hwp5html 실행파일을 찾을 수 없습니다 (pyhwp 미설치).")
        return
    except subprocess.TimeoutExpired:
        print("hwp5html 타임아웃(120초). 문서가 예상보다 큽니다.")
        return

    if proc.returncode != 0:
        print("hwp5html 변환 실패:")
        print(proc.stderr[:1000])
        return

    index_html = OUT_DIR / "index.xhtml"
    if not index_html.exists():
        candidates = list(OUT_DIR.glob("*.xhtml")) + list(OUT_DIR.glob("*.html"))
        if not candidates:
            print(f"변환 결과 파일을 {OUT_DIR}에서 찾지 못했습니다. 폴더 내용물:")
            for p in OUT_DIR.iterdir():
                print(" ", p.name)
            return
        index_html = candidates[0]

    print(f"변환 결과 저장됨: {OUT_DIR} (index.xhtml 등 - 직접 열어봐도 됨)")
    css_files = list(OUT_DIR.glob("*.css"))
    print(f"CSS 파일: {[p.name for p in css_files]}")

    xhtml_text = index_html.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(xhtml_text, "lxml-xml")

    all_tables = soup.find_all("table")
    print(f"\n문서 전체에서 발견된 <table>: {len(all_tables)}개")

    # 추진일정표로 추정되는 표 찾기: 표 안 텍스트에 힌트 키워드가 들어있는지 확인
    candidates = []
    for i, table_tag in enumerate(all_tables):
        table_text = table_tag.get_text(" ", strip=True)
        if any(kw.replace(" ", "") in table_text.replace(" ", "") for kw in _TABLE_HINT_KEYWORDS):
            candidates.append((i, table_tag))

    if not candidates:
        print("\n'추진 내용'/'M+1' 등 힌트 키워드가 포함된 표를 찾지 못했습니다.")
        print("전체 표 목록의 첫 60자씩 미리보기:")
        for i, t in enumerate(all_tables):
            preview = t.get_text(" ", strip=True)[:60]
            print(f"  [{i}] {preview}")
        return

    print(f"\n추진일정표로 추정되는 표: {len(candidates)}개 (인덱스 {[i for i, _ in candidates]})")

    # CSS 규칙 미리 파싱: class -> {property: value}
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

    bg_related_css = {sel: props for sel, props in css_rules.items()
                       if any("background" in k for k in props)}
    print(f"\nCSS에서 background 관련 규칙 {len(bg_related_css)}개 발견 (일부만 출력):")
    for sel, props in list(bg_related_css.items())[:20]:
        print(f"  {sel} -> {props}")

    for idx, table_tag in candidates:
        print("\n" + "=" * 100)
        print(f"[표 #{idx}] 전체 셀 속성 덤프")
        for r_i, tr in enumerate(table_tag.find_all("tr")):
            cells = tr.find_all(["td", "th"])
            for c_i, cell in enumerate(cells):
                text = cell.get_text(strip=True)
                attrs = dict(cell.attrs)
                print(f"  행{r_i} 셀{c_i} <{cell.name}> text={text!r} attrs={attrs}")

    print("\n" + "=" * 100)
    print("확인 포인트:")
    print("1) 색칠된(활성 기간) 셀들의 attrs에 style(background-color:...) / class / bgcolor 중 뭐가 찍히는지")
    print("2) class가 찍힌다면, 위 'background 관련 CSS 규칙' 목록에서 그 class에 실제 배경색이 있는지")
    print("3) 색칠 안 된 셀과 색칠된 셀의 attrs가 실제로 다른 값을 갖는지(구분 가능한지)")
    print("이 결과를 참고해서, 그 기준으로 hwp_parser.extract_tables()를 고쳐서")
    print("'셀이 칠해졌는지'까지 표 데이터에 포함시키는 방법을 같이 설계할 수 있음.")


if __name__ == "__main__":
    main()
