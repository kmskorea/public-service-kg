"""web/index.html → 미리보기(claude.ai Artifact)용 페이지 본문.

Artifact 는 게시할 때 <!doctype>·<html>·<head>·<body> 틀과 charset·viewport meta 를 스스로 씌우므로,
저장소의 완전한 문서(GitHub Pages·로컬 서버용)에서 그 부분만 빼서 내보낸다. 내용은 같다.

    python scripts/build_web_artifact.py <출력 경로>
judge.js · app.js · data/kg.json 은 페이지 옆에 같은 경로로 함께 올린다.
"""
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "web" / "index.html"
DROP = [r"<!doctype html>\s*", r"<html[^>]*>\s*", r"</html>\s*", r"<head>\s*", r"</head>\s*", r"<body>\s*",
        r"</body>\s*", r'<meta charset="utf-8">\s*', r'<meta name="viewport"[^>]*>\s*']


def build(src: Path = SRC) -> str:
    html = src.read_text(encoding="utf-8")
    for pat in DROP:
        html = re.sub(pat, "", html, flags=re.I)
    return html.lstrip()


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(out)
