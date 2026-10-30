from pathlib import Path
from enum import Enum
import typer
from .config import HOLE_ACTIONS, load_config
from .engine import run_check
from .report import write_reports, summary_text

app = typer.Typer(help="纯本地面要素拓扑检查工具", add_completion=False)


class FailOn(str, Enum):
    error = "error"
    warn = "warn"
    never = "never"


@app.callback()
def main():
    pass


@app.command()
def web(port: int = typer.Option(8765, min=1, max=65535),
        open_browser: bool = typer.Option(True, "--open/--no-open")):
    """启动本地网页操作界面。"""
    from .web import serve
    serve(port, open_browser)


@app.command()
def check(input_path: Path = typer.Argument(..., exists=True, dir_okay=False),
          layer: str | None = typer.Option(None), id_field: str | None = typer.Option(None),
          config: Path | None = typer.Option(None, exists=True, dir_okay=False),
          out: Path | None = typer.Option(None), fail_on: FailOn = typer.Option(FailOn.never),
          repair: bool = typer.Option(False, "--repair", help="生成修复候选和人工审核数据，不写回输入"),
          simple: bool = typer.Option(False, "--simple", help="自动米制投影、0.1亩碎面合并和切洞预设"),
          hole_action: str | None = typer.Option(
              None, "--hole-action",
              help="岛洞处理方式：slit=0.1毫米细缝 / cut_open=1厘米开缝 / split_open=零宽切分 / fill=填平 / legacy=不处理")):
    try:
        cfg = load_config(config)
        if simple:
            cfg.simple_mode = True
        if hole_action is not None:
            if hole_action not in HOLE_ACTIONS:
                raise ValueError(f"未知的岛洞处理方式：{hole_action}（可选 {', '.join(HOLE_ACTIONS)}）")

        if id_field is not None:
            cfg.input.id_field = id_field
        layer = layer if layer is not None else cfg.input.layer
        output = out if out is not None else Path(cfg.output_dir)
        # Prevent report files from overwriting the input dataset.
        if any(input_path.resolve() == (output / name).resolve() for name in ("report.json", "issues.geojson", "report.csv", "summary.txt")):
            raise ValueError("输出报告路径与输入文件冲突")
        report = run_check(input_path, layer, cfg, repair=repair, output_dir=output,
                           hole_action=hole_action)
        write_reports(report, output)
    except Exception as exc:
        typer.echo(f"检查失败: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(summary_text(report), nl=False)
    typer.echo(f"输出目录: {output.resolve()}")
    if repair:
        typer.echo("修复输出: repair_report.json / repair_staging.gpkg（待审核候选未自动应用）")
    if fail_on != FailOn.never and any(i.severity == "ERROR" or (fail_on == FailOn.warn and i.severity == "WARN") for i in report.issues):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
