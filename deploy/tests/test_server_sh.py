"""server.sh 테스트 (AGENTS.md 10절). 실제 컨테이너 대신 가짜 docker·curl 을 PATH 앞에 둔다.

가짜 docker: `compose up` 때 .env 의 TAG·MODEL_VERSION 을 '떠 있는 상태' 파일에 옮긴다.
가짜 curl : 떠 있는 판이 FAKE_BROKEN 목록에 있으면 실패(컨테이너가 못 뜬 상황), 아니면 /health 응답을 낸다.
"""
import os
import stat
import subprocess
import textwrap

import pytest

SERVER_SH = os.path.join(os.path.dirname(__file__), "..", "lightsail", "server.sh")

FAKE_DOCKER = textwrap.dedent("""\
    #!/bin/bash
    echo "docker $*" >> "$FAKE_LOG"
    if [ "$1" = compose ] && [ "$2" = up ]; then
        grep -E '^(TAG|MODEL_VERSION|MODEL_DIR)=' .env > "$FAKE_STATE" || : > "$FAKE_STATE"
    fi
    if [ "$1" = image ]; then exit "${FAKE_IMAGE_MISSING:-0}"; fi
    if [ "$1" = login ]; then cat > /dev/null; fi
    exit 0
""")

FAKE_CURL = textwrap.dedent("""\
    #!/bin/bash
    ver=$(grep '^MODEL_VERSION=' "$FAKE_STATE" 2>/dev/null | cut -d= -f2)
    tag=$(grep '^TAG=' "$FAKE_STATE" 2>/dev/null | cut -d= -f2)
    for b in $FAKE_BROKEN; do
        if [ "$b" = "${ver:-baked}" ] || [ "$b" = "$tag" ]; then exit 22; fi
    done
    echo "{\\"status\\":\\"ok\\",\\"model\\":\\"onnx\\",\\"app_version\\":\\"${tag:-dev}\\",\\"model_version\\":\\"${ver:-baked}\\"}"
""")


@pytest.fixture
def srv(tmp_path):
    home, bin_ = tmp_path / "srv", tmp_path / "bin"
    home.mkdir()
    bin_.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
        p = bin_ / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "PATH": f"{bin_}:{os.environ['PATH']}",
        "LOL_HOME": str(home),
        "FAKE_LOG": str(tmp_path / "docker.log"),
        "FAKE_STATE": str(tmp_path / "running"),
        "FAKE_BROKEN": "",
        "HEALTH_TRIES": "2",
        "HEALTH_INTERVAL": "0",
    }
    return home, env, tmp_path


def make_version(home, ver):
    d = home / "models" / ver
    d.mkdir(parents=True)
    for f in ("model_sklearn.onnx", "model.json", "baseline.json"):
        (d / f).write_text("{}")


def run(env, *args, stdin=""):
    return subprocess.run(["bash", SERVER_SH, *args], env=env, input=stdin, text=True, capture_output=True)


def dotenv(home) -> dict:
    lines = (home / ".env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def running(tmp_path) -> dict:
    lines = (tmp_path / "running").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def test_model_promote(srv):
    home, env, tmp = srv
    make_version(home, "m20261118-1")
    r = run(env, "model", "m20261118-1")
    assert r.returncode == 0, r.stderr
    assert dotenv(home)["MODEL_VERSION"] == "m20261118-1"
    assert dotenv(home)["MODEL_DIR"] == "/models/m20261118-1"
    assert '"model_version":"m20261118-1"' in r.stdout


def test_model_failure_restores_previous_dir(srv):
    """새 판이 안 뜨면 MODEL_DIR 을 /models/<이전 판> 으로 되돌린다 (새 판 경로로 남기면 안 된다)."""
    home, env, tmp = srv
    make_version(home, "m20261118-1")
    make_version(home, "m20261118-2")
    assert run(env, "model", "m20261118-1").returncode == 0
    r = run({**env, "FAKE_BROKEN": "m20261118-2"}, "model", "m20261118-2")
    assert r.returncode == 1
    assert dotenv(home)["MODEL_VERSION"] == "m20261118-1"
    assert dotenv(home)["MODEL_DIR"] == "/models/m20261118-1"
    assert running(tmp)["MODEL_DIR"] == "/models/m20261118-1"  # 되돌린 설정으로 다시 띄웠다


def test_model_failure_without_previous_falls_back_to_baked(srv):
    home, env, tmp = srv
    (home / ".env").write_text("IMAGE=ghcr.io/x/y\nTAG=v1.0\n")
    make_version(home, "m20261118-1")
    r = run({**env, "FAKE_BROKEN": "m20261118-1"}, "model", "m20261118-1")
    assert r.returncode == 1
    e = dotenv(home)
    assert "MODEL_VERSION" not in e and "MODEL_DIR" not in e
    assert e["TAG"] == "v1.0"  # 코드 판은 건드리지 않는다


@pytest.mark.parametrize("ver", ["m2026111-1", "m20261118", "20261118-1", "m20261118-1;touch x", "../m20261118-1"])
def test_model_rejects_bad_version(srv, ver):
    home, env, tmp = srv
    r = run(env, "model", ver)
    assert r.returncode == 1
    assert not (tmp / "docker.log").exists()  # docker 를 한 번도 부르지 않았다


def test_model_rejects_missing_files(srv):
    home, env, tmp = srv
    make_version(home, "m20261118-1")
    (home / "models" / "m20261118-1" / "baseline.json").unlink()
    r = run(env, "model", "m20261118-1")
    assert r.returncode == 1 and "baseline.json" in r.stderr


def test_image_deploy_and_rollback_on_failure(srv):
    home, env, tmp = srv
    assert run(env, "image", "v1.0", "ghcr.io/owner/repo", stdin="\n").returncode == 0
    assert dotenv(home)["TAG"] == "v1.0"
    r = run({**env, "FAKE_BROKEN": "v1.1"}, "image", "v1.1", "ghcr.io/owner/repo", stdin="\n")
    assert r.returncode == 1
    assert dotenv(home)["TAG"] == "v1.0"
    assert running(tmp)["TAG"] == "v1.0"


def test_image_token_only_via_stdin(srv):
    home, env, tmp = srv
    secret = "ghp_not_a_real_token_123"
    r = run({**env, "FAKE_IMAGE_MISSING": "1"}, "image", "v1.0", "ghcr.io/owner/repo", stdin=secret + "\n")
    assert r.returncode == 0, r.stderr
    log = (tmp / "docker.log").read_text()
    assert "login ghcr.io -u token --password-stdin" in log
    assert "pull -q ghcr.io/owner/repo/lol-api:v1.0" in log
    assert secret not in log  # 토큰이 명령 인자에 남지 않는다


@pytest.mark.parametrize("tag", ["1.0", "v1.0-rc", "latest", "v1..0"])
def test_image_rejects_bad_tag(srv, tag):
    home, env, tmp = srv
    assert run(env, "image", tag, "ghcr.io/owner/repo", stdin="\n").returncode == 1


def test_unknown_command(srv):
    home, env, tmp = srv
    assert run(env, "nope").returncode == 2
