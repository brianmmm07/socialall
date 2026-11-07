import importlib.util
import plistlib
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from socialall.app import create_app
from socialall.desktop import autostart, launcher
from socialall.desktop.__main__ import parser
from socialall.desktop.server import LocalOnly, add_app_routes
from socialall.storage import Store

ROOT = Path(__file__).resolve().parent.parent


def make_client(tmp_path, port=8000):
    cfg = launcher.Settings(root=tmp_path, port=port)
    app = create_app(store=Store(":memory:"), client=httpx.AsyncClient(), cache_dir=str(tmp_path / "videos"))
    add_app_routes(app, cfg)
    return app, TestClient(LocalOnly(app, port), base_url=f"http://127.0.0.1:{port}")


def test_ping_identifies_socialall(tmp_path):
    _, client = make_client(tmp_path)
    with client:
        data = client.get("/api/app/ping").json()
    assert data["app"] == "socialall"
    assert data["build"] == launcher.code_id()
    assert data["stale"] is False


def test_shutdown_needs_launcher_header_and_a_server(tmp_path):
    app, client = make_client(tmp_path)
    with client:
        assert client.post("/api/app/shutdown").status_code == 403
        assert client.post("/api/app/shutdown", headers={"X-SocialAll-App": "1"}).status_code == 409

        class FakeServer:
            should_exit = False

        app.state.server = FakeServer()
        assert client.post("/api/app/shutdown", headers={"X-SocialAll-App": "1"}).status_code == 200
        assert app.state.server.should_exit is True


def test_guard_blocks_other_hosts_and_cross_site_changes(tmp_path):
    _, client = make_client(tmp_path)
    with client:
        assert client.get("/api/topics", headers={"Host": "evil.example"}).status_code == 403
        r = client.post("/api/topics", json={"name": "cats"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        r = client.post("/api/topics", json={"name": "cats"}, headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403
        r = client.post("/api/topics", json={"name": "cats"}, headers={"Origin": "http://127.0.0.1:8000"})
        assert r.status_code == 201
        assert client.get("/api/topics", headers={"Host": "localhost:8000"}).status_code == 200


def test_page_links_the_manifest_and_icons(tmp_path):
    _, client = make_client(tmp_path)
    with client:
        page = client.get("/").text
        assert 'rel="manifest"' in page
        manifest = client.get("/static/manifest.webmanifest").json()
        for icon in manifest["icons"]:
            assert client.get(icon["src"]).status_code == 200
    assert manifest["name"] == launcher.APP_NAME


def test_save_port_replaces_or_appends(tmp_path):
    env = tmp_path / ".env"
    launcher.save_port(env, 8001)
    assert env.read_text() == "SOCIALALL_PORT=8001\n"
    env.write_text("X_BEARER_TOKEN=abc\nSOCIALALL_PORT=8001\n# note\n")
    launcher.save_port(env, 8002)
    assert env.read_text() == "X_BEARER_TOKEN=abc\nSOCIALALL_PORT=8002\n# note\n"


def test_load_settings_reads_port_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("SOCIALALL_PORT", raising=False)
    (tmp_path / ".env").write_text("SOCIALALL_PORT=8123\n")
    cfg = launcher.load_settings(root=tmp_path)
    assert cfg.port == 8123
    assert cfg.base_url == "http://127.0.0.1:8123"
    monkeypatch.delenv("SOCIALALL_PORT", raising=False)
    assert launcher.load_settings(port=9000, root=tmp_path).port == 9000


def test_probe_finds_nothing_on_a_closed_port(tmp_path):
    port = launcher.free_port(launcher.Settings(root=tmp_path, port=20000))
    assert launcher.probe(launcher.Settings(root=tmp_path, port=port), timeout=0.5) is None


def test_launch_agent_runs_the_server_from_the_project(tmp_path):
    cfg = launcher.Settings(root=tmp_path)
    plist = plistlib.loads(autostart.launch_agent(cfg))
    assert plist["Label"] == autostart.LABEL
    assert plist["ProgramArguments"][1:] == ["-m", "socialall.desktop", "serve"]
    assert plist["WorkingDirectory"] == str(tmp_path)
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["RunAtLoad"] is True


def test_commands_default_to_launch():
    args = parser().parse_args([])
    assert args.cmd is None
    args = parser().parse_args(["autostart", "on", "--port", "8010"])
    assert (args.cmd, args.action, args.port) == ("autostart", "on", 8010)


def test_bootstrap_reinstalls_when_pyproject_changes(tmp_path):
    spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts" / "bootstrap.py")
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    stamp = tmp_path / "stamp"
    assert bootstrap.needs_install(stamp)
    stamp.write_text(bootstrap.project_hash())
    assert not bootstrap.needs_install(stamp)


def test_mac_launcher_is_executable():
    for name in ("Start SocialAll.command", "start-socialall.sh"):
        assert (ROOT / name).stat().st_mode & 0o111
