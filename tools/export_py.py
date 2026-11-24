"""Export the notebooks in notebooks/ to runnable scripts in src/ (percent format: '# %%' cells, readable in VS Code,
Spyder and Jupytext). The notebooks are the version run on TIP; the scripts are the same code, cell by cell.

    python tools/export_py.py            # all notebooks
    python src/03_landscape.py           # then run a script from anywhere inside the repository

Shell and magic lines ('%', '!') are commented out; `display` falls back to print outside Jupyter.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = '''"""{title}

Exported from notebooks/{nb} by tools/export_py.py: do not edit by hand, edit the notebook and export again.
Run from anywhere inside the repository:  python src/{py}
"""
# %%
try:                                   # inside Jupyter / IPython
    from IPython.display import display
except ImportError:                    # plain Python
    def display(*objs):
        for o in objs:
            print(o.to_string() if hasattr(o, "to_string") else o)
'''

TITLES = {
    "01_extract": "Step 0 · sector definition and extraction (SQL queries run on TIP)",
    "02_clean": "Step 0 · clean datasets: families, applicants, dates, citations, legal events",
    "03_landscape": "Steps 1-2 · sector landscape and competitive landscape",
}


def export(nb_path: Path) -> Path:
    cells = json.loads(nb_path.read_text(encoding="utf-8"))["cells"]
    stem = nb_path.stem
    out = [HEADER.format(title=TITLES.get(stem, stem), nb=nb_path.name, py=f"{stem}.py")]
    for c in cells:
        src = "".join(c["source"]) if isinstance(c["source"], list) else c["source"]
        if not src.strip():
            continue
        if c["cell_type"] == "markdown":
            body = "\n".join(("# " + l) if l.strip() else "#" for l in src.splitlines())
            out.append("# %% [markdown]\n" + body + "\n")
        elif c["cell_type"] == "code":
            lines = [("# " + l) if l.lstrip().startswith(("%", "!")) else l for l in src.splitlines()]
            out.append("# %%\n" + "\n".join(lines) + "\n")
    py = ROOT / "src" / f"{stem}.py"
    py.write_text("\n".join(out), encoding="utf-8")
    return py


if __name__ == "__main__":
    names = sys.argv[1:] or sorted(p.name for p in (ROOT / "notebooks").glob("*.ipynb"))
    for n in names:
        p = export(ROOT / "notebooks" / n)
        compile(p.read_text(encoding="utf-8"), str(p), "exec")      # syntax check
        print(f"{n} -> {p.relative_to(ROOT)}")
