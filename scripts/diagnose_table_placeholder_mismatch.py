""""<표>" 자리표시자 개수와 hwp5html이 뽑은 <table> 개수가 왜 다른지
원인을 파고드는 진단 스크립트.

[배경] verify_inline_table_insertion.py로 국방과학연구소_기록관리시스템...
문서를 검증했더니 자리표시자(hwp5txt 본문에 남은 "<표>" 줄) 109개, 실제
추출된 표(hwp5html의 <table> 태그) 111개로 2개가 안 맞아서 merge_one()이
안전하게 폴백(문서 끝 일괄 첨부)했다. _insert_tables_inline()의 docstring
(merge_text.py)에 이미 적어뒀듯, 이 개수 일치는 "그럴 것으로 기대"하는
가정이지 100% 보장된 계약이 아니어서 실제로 어긋나는 경우가 나온 것 -
왜 어긋나는지 알아야 이 표(들)를 다른 방법으로 위치시킬지, 그냥 폴백을
받아들일지 판단할 수 있다.

가장 유력한 가설 두 가지를 확인한다:
  1. 중첩 표(표 안에 표) - hwp5html은 BeautifulSoup으로 <table>을 재귀적
     으로 다 찾아내니까 중첩된 표까지 별도 표로 세지만, hwp5txt(본문
     텍스트, plaintext.xsl)는 중첩 표에 대해 "<표>" 자리표시자를 별도로
     안 만들 수 있다(바깥 표 자리표시자 하나로 퉁쳐질 가능성). 표 2개가
     중첩돼 있으면 딱 이 문서의 차이(2개)와 맞아떨어진다.
  2. 머리말/꼬리말/각주처럼 본문 흐름 밖에 있는 표 - hwp5html은 문서
     전체를 훑어 표를 찾지만, hwp5txt는 본문(paragraph) 흐름만 뽑고
     머리말/꼬리말/각주는 건너뛸 수 있다. 이런 표가 있으면 hwp5html
     쪽에만 추가로 잡힌다.

두 가설 다 자동으로 확인해서 사람이 눈으로 대조하기 쉽게 출력한다.

[참고] 반드시 한빈 로컬(파이참)에서 실행해야 함 - 공용 개발 환경엔
원본 파일이 없다.

파이참에서 우클릭 > Run으로 실행하면 된다(인자 불필요).
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIR, RAW_FILES_DIR  # noqa: E402

DOC_FILE_NAME = "국방과학연구소_기록관리시스템 통합 활용 및 보안 환경 구축.hwp"
OUT_DIR = OUTPUT_DIR / "gantt_diag"  # 이전 진단에서 이미 변환해둔 게 있으면 재사용


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
        return

    # --- 1. hwp5txt: <표> 자리표시자 개수/위치 ---
    print("hwp5txt로 본문 재추출 중...")
    try:
        proc_txt = _run(["hwp5txt", str(hwp_path)], timeout=60)
    except FileNotFoundError:
        print("hwp5txt 실행파일을 찾을 수 없습니다.")
        return
    if proc_txt.returncode != 0:
        print("hwp5txt 실패:", proc_txt.stderr[:500])
        return
    raw_text = proc_txt.stdout
    lines = raw_text.split("\n")
    placeholder_line_idxs = [i for i, ln in enumerate(lines) if ln.strip() == "<표>"]
    print(f"'<표>' 자리표시자 개수: {len(placeholder_line_idxs)}개")

    # --- 2. hwp5html: <table> 개수 + 중첩 여부 ---
    index_html = OUT_DIR / "index.xhtml"
    if not index_html.exists():
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        print("hwp5html 변환 중... (최대 90초)")
        try:
            proc_html = _run(["hwp5html", "--output", str(OUT_DIR), str(hwp_path)], timeout=120)
        except FileNotFoundError:
            print("hwp5html 실행파일을 찾을 수 없습니다.")
            return
        if proc_html.returncode != 0:
            print("hwp5html 실패:", proc_html.stderr[:500])
            return
        index_html = OUT_DIR / "index.xhtml"
    else:
        print(f"기존 변환 결과 재사용: {OUT_DIR}")

    soup = BeautifulSoup(index_html.read_text(encoding="utf-8", errors="ignore"), "lxml-xml")
    all_tables = soup.find_all("table")
    print(f"<table> 총 개수: {len(all_tables)}개")

    # 가설 1: 중첩 표 - 어떤 표가 다른 표의 조상(ancestor)으로 <table>을 갖는지 확인
    nested = []
    top_level = []
    for i, t in enumerate(all_tables):
        parent_table = t.find_parent("table")
        if parent_table is not None:
            nested.append(i)
        else:
            top_level.append(i)
    print(f"\n[가설 1: 중첩 표] 다른 표 안에 들어있는 표: {len(nested)}개 (인덱스 {nested})")
    print(f"최상위(중첩 아닌) 표: {len(top_level)}개")
    if len(top_level) == len(placeholder_line_idxs):
        print(">> 최상위 표 개수가 자리표시자 개수와 정확히 일치! 중첩 표가 원인일 가능성이 높음.")
    else:
        print(f">> 최상위 표 개수({len(top_level)})도 자리표시자 개수({len(placeholder_line_idxs)})와 다름 - 중첩만으로는 설명 안 됨, 가설 2 확인 필요.")

    for i in nested:
        t = all_tables[i]
        preview = t.get_text(" ", strip=True)[:60]
        parent_i = all_tables.index(t.find_parent("table"))
        print(f"    표#{i}는 표#{parent_i} 안에 중첩됨. 미리보기: {preview!r}")

    # 가설 2: 본문 흐름 밖(머리말/꼬리말/각주 등)에 있는 표 - HeaderFooter/Footnote 등
    # 이름이 들어간 조상 태그가 있는지 확인(pyhwp/hwp5html이 실제로 이런 이름을
    # 쓰는지는 문서마다 다를 수 있어 가능한 후보를 넓게 잡는다).
    print("\n[가설 2: 본문 흐름 밖(머리말/꼬리말/각주 등) 표]")
    _OUT_OF_FLOW_HINTS = ["header", "footer", "footnote", "endnote", "annotation"]
    out_of_flow = []
    for i, t in enumerate(all_tables):
        ancestor_names = [p.name.lower() for p in t.find_parents() if p.name]
        if any(hint in name for name in ancestor_names for hint in _OUT_OF_FLOW_HINTS):
            out_of_flow.append((i, ancestor_names))
    if out_of_flow:
        print(f"본문 흐름 밖으로 추정되는 표: {len(out_of_flow)}개")
        for i, names in out_of_flow:
            print(f"    표#{i} 조상 태그: {names}")
    else:
        print("머리말/꼬리말/각주 관련 태그 이름을 가진 표는 못 찾음(이 가설은 약함 - 문서 구조가 다를 수 있음).")

    # --- 3. 정리 ---
    print("\n" + "=" * 100)
    print("요약:")
    print(f"  <표> 자리표시자: {len(placeholder_line_idxs)}개")
    print(f"  <table> 총합: {len(all_tables)}개 (최상위 {len(top_level)}개 + 중첩 {len(nested)}개)")
    print(f"  본문 흐름 밖 추정: {len(out_of_flow)}개")
    print("이 출력을 참고하면, 이 표들을 위치시킬 방법(예: 중첩 표는 부모 표")
    print("자리에 같이 끼워넣기)을 설계할지, 그냥 지금의 안전한 폴백(문서 끝 일괄 첨부)을")
    print("받아들일지 같이 판단하자.")


if __name__ == "__main__":
    main()
