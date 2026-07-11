"""
app.py — CogniCode Flask web interface.
"""

import os
import tempfile
import shutil
from pathlib import Path

from flask import Flask, render_template, request

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

_predictor = None

def get_predictor():
    global _predictor
    if _predictor is None:
        import predictor as p
        _predictor = p
    return _predictor


def _sonar_kwargs(form):
    return {
        "sonar_host":     form.get("sonar_host", "").strip(),
        "sonar_user":     form.get("sonar_user", "admin").strip(),
        "sonar_password": form.get("sonar_password", "admin").strip(),
        "sonar_token":    form.get("sonar_token", "").strip(),
        "und_bin":        form.get("und_bin", "").strip(),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    mode   = request.form.get("mode", "github")
    p      = get_predictor()
    kwargs = _sonar_kwargs(request.form)
    result = None

    if mode == "github":
        repo_url = request.form.get("repo_url", "").strip()
        if not repo_url:
            return render_template("index.html", error="Please enter a GitHub URL.")
        result = p.predict_from_github(repo_url, **kwargs)
        result["source"] = repo_url

    elif mode == "path":
        local_path = request.form.get("local_path", "").strip()
        if not local_path:
            return render_template("index.html", error="Please enter a local path.")
        path = Path(local_path)
        if not path.exists():
            return render_template("index.html", error=f"Path not found: {local_path}")
        result = p.predict_file(path, **kwargs) if path.is_file() \
                 else p.predict_directory(path, **kwargs)
        result["source"] = local_path

    elif mode == "upload":
        uploaded = request.files.get("py_file")
        if not uploaded or uploaded.filename == "":
            return render_template("index.html", error="Please select a .py file.")
        if not uploaded.filename.endswith(".py"):
            return render_template("index.html", error="Only .py files are supported.")
        tmp_dir  = tempfile.mkdtemp(prefix="cognicode_upload_")
        tmp_path = Path(tmp_dir) / uploaded.filename
        uploaded.save(str(tmp_path))
        result = p.predict_file(tmp_path, **kwargs)
        result["source"] = uploaded.filename
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if result and "error" in result:
        return render_template("index.html", error=result["error"])

    return render_template("result.html", result=result)


if __name__ == "__main__":
    app.run(debug=True, port=5050)
