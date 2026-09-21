"""지식베이스 빌드 CLI (04_system_design.md §4-3).

    uv run kb build                 전체 실행
    uv run kb build --only parse    한 단계만
    uv run kb report                마지막 빌드의 검증 결과
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from kb_build.paths import ARTIFACTS_DIR
from kb_build.pipeline import new_build_id, run_chunk, run_fix, run_parse

app = typer.Typer(add_completion=False, help="소비자 분쟁 지식베이스 빌드")


def _latest_build_id() -> str:
    builds = sorted(p.name for p in ARTIFACTS_DIR.glob("kb_*") if p.is_dir())
    if not builds:
        raise typer.BadParameter("빌드가 없습니다. 먼저 `kb build`를 실행하세요.")
    return builds[-1]


@app.command()
def build(
    only: Annotated[str | None, typer.Option(help="parse | fix | chunk | embed | graph")] = None,
    build_id: Annotated[str | None, typer.Option("--build-id", help="이어서 실행할 빌드 ID")] = None,
    limit: Annotated[int | None, typer.Option(help="임베딩·그래프를 앞 N개 청크로 제한 (시험용)")] = None,
) -> None:
    """원문에서 지식베이스 산출물을 만든다."""
    stages = ["parse", "fix", "chunk", "embed", "graph"] if only is None else [only]
    current = build_id or (new_build_id() if "parse" in stages else _latest_build_id())
    typer.echo(f"빌드 ID: {current}")

    if "parse" in stages:
        tables, report = run_parse(current)
        typer.echo(
            f"[parse] 표 {report.tables_total}개 · 검증 통과 {report.tables_rule_ok}개 · "
            f"실패 {len(report.failures)}개"
        )
        for failure in report.failures[:10]:
            typer.echo(f"  - {failure['table_id']} {failure['title'][:30]} :: {failure['why']}")

    if "fix" in stages:
        _, report, cost = run_fix(current)
        typer.echo(
            f"[fix] LLM 보정 성공 {report.tables_llm_fixed}개 · 남은 실패 {len(report.failures)}개 · "
            f"비용 ${cost:.4f}"
        )

    if "chunk" in stages:
        chunks, report = run_chunk(current)
        typer.echo(
            f"[chunk] 청크 {report.chunk_counts['total']}개 "
            f"(법령 {report.chunk_counts['law']} · 기준 {report.chunk_counts['standard']}) · "
            f"통째 청크 표 {report.tables_whole}개"
        )
        lengths = sorted(len(c.embed_text) for c in chunks)
        mid = lengths[len(lengths) // 2]
        typer.echo(f"[chunk] 임베딩 텍스트 길이 중앙값 {mid}자 · 최대 {lengths[-1]}자")

    if "embed" in stages:
        from kb_build.embed import embed_chunks
        from kb_build.pipeline import load_chunks

        items = load_chunks(current)
        if limit:
            items = items[:limit]
        tokens, cost = embed_chunks(
            current, items, progress=lambda i, n: typer.echo(f"  임베딩 {i}/{n}", nl=True) if i % 480 == 0 else None
        )
        typer.echo(f"[embed] 청크 {len(items)}개 · 토큰 {tokens:,} · 비용 ${cost:.4f}")

    if "graph" in stages:
        from kb_build.graph import build_graph
        from kb_build.pipeline import load_chunks

        items = load_chunks(current)
        if limit:
            items = items[:limit]
        _, cost, stats = build_graph(
            current, items, progress=lambda i, n: typer.echo(f"  그래프 {i}/{n}") if i % 50 == 0 else None
        )
        typer.echo(
            f"[graph] 노드 {stats['nodes']:,}개 · 엣지 {stats['edges']:,}개 · "
            f"군집 {stats['communities']}개 · 비용 ${cost:.4f}"
        )


@app.command()
def report(build_id: Annotated[str | None, typer.Option("--build-id")] = None) -> None:
    """빌드 리포트를 출력한다."""
    current = build_id or _latest_build_id()
    path = ARTIFACTS_DIR / current / "parse_report.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    typer.echo(json.dumps(data, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    app()
