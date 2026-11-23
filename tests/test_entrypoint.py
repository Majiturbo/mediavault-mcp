"""Entry-point wiring: main() starts the stdio transport."""

from mediavault import __main__


def test_main_runs_stdio(monkeypatch):
    seen = {}

    def fake_run(transport):
        seen["transport"] = transport

    monkeypatch.setattr(__main__.mcp, "run", fake_run)
    __main__.main()
    assert seen["transport"] == "stdio"
