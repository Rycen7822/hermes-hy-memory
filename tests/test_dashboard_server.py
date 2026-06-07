from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from config import load_hy_memory_config
from dashboard import build_server


def _server_for_tmp_home(tmp_path: Path):
    config = load_hy_memory_config(tmp_path, {"agent_identity": "agent-a", "user_id": "user-a", "session_id": "session-a"})
    server = build_server(config, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://{server.server_address[0]}:{server.server_address[1]}"


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        assert response.headers.get_content_type() == "application/json"
        return json.loads(response.read().decode("utf-8"))


def test_dashboard_serves_html_and_json_endpoints(tmp_path):
    server, thread, base_url = _server_for_tmp_home(tmp_path)
    try:
        with urllib.request.urlopen(base_url + "/", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "Current Memory Overview" in html
        assert "Memory Usage" in html
        assert "Recent Activity" in html
        assert "Current Structured Memory Records" in html
        assert "Memory Records shows active structured records from local vector metadata" in html
        assert "Raw / History Memory Records" in html
        assert "historyLayerFilter" in html
        assert "/api/history-records" in html
        assert "History raw L1" in html
        assert "History L1 / L3" not in html
        assert "Current L3 records" in html
        assert 'id="sectionNav"' in html
        assert 'data-view="activity"' in html
        assert 'data-view="memories"' in html
        assert 'data-view="history"' in html
        assert 'data-page-size="25"' in html
        assert "function renderPager" in html
        assert "function shortText" in html
        assert "line-clamp" in html
        assert 'id="overview" class="overview-mount"' in html
        assert "function overviewStat" in html
        assert "function overviewLayerRows" in html
        assert "function overviewEventRows" in html
        assert "function sourceNode" in html
        assert "data-overview-hero" in html
        assert "data-overview-matrix" in html
        assert "data-overview-layers" in html
        assert "data-overview-events" in html
        assert ".overview-console { display: grid;" in html
        assert ".overview-hero { position: relative;" in html
        assert ".overview-value { max-width: 100%; min-width: 0; overflow: hidden;" in html
        assert ".overview-value-compact" in html
        assert "function compactTimestamp" in html
        assert "function localOffsetLabel" in html
        assert "function utcDateFromTimestamp" in html
        assert "function beijingTimestamp" in html
        assert "timeZone: 'Asia/Shanghai'" in html
        assert "北京时间" in html
        assert "new Date(text)" in html
        assert "getTimezoneOffset" in html
        assert "${match[2]}-${match[3]} ${match[4]}:${match[5]}Z" not in html
        assert "function overviewPathRow(label, value, titleValue = value)" in html
        assert "overviewStat('History raw L1', formatCount(historyL1), 'raw l1 events from history table')" in html
        assert "const historyL3" not in html
        assert "overviewStat('Latest search', compactTimestamp(latestSearch), 'most recent recall timestamp', latestSearch)" in html
        assert "overviewPathRow('Latest search', localTimestamp(latestSearch, true), latestSearch)" in html
        assert ".overview-path-value { max-width: 100%; min-width: 0; overflow: hidden;" in html
        assert "overviewStat('History events', formatCount(data.totals.history_events), 'history table events')" in html
        assert "historical l3 events" not in html
        assert "overflow-wrap: anywhere" in html
        assert "function metric(label, value)" not in html
        assert 'id="overview" class="grid"' not in html
        assert ".metric-card {" not in html
        assert "--accent-0: #ef6f2e" in html
        assert "--bg-0: #050505" in html
        assert "--surface-1: #101010" in html
        assert "--line-1: rgba(184,179,176,.18)" in html
        assert 'id="usageLegend"' in html
        assert 'aria-label="Memory usage color legend"' in html
        assert 'class="toolbar usage-toolbar"' in html
        assert html.index('id="bucket"') < html.index('id="usageLegend"') < html.index('id="usage"')
        assert 'data-legend-key="add"' in html
        assert 'data-legend-key="search"' in html
        assert 'data-legend-key="update"' in html
        assert 'data-legend-key="delete"' in html
        assert 'data-legend-key="recall_pipeline"' in html
        assert "Add" in html
        assert "Search" in html
        assert "Update" in html
        assert "Delete" in html
        assert "Recall pipeline" in html
        assert ".usage-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;" in html
        assert ".usage-legend { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end;" in html
        assert "margin: 2px 0 var(--space-4); padding: 10px 12px; border: 1px solid var(--line-0)" not in html
        assert ".legend-swatch { width: 18px; height: 8px;" in html
        assert 'class="topbar-blur"' in html
        assert ".topbar { position: fixed; inset: 0 0 auto; z-index: 60; height: 72px;" in html
        assert "height: 124px; background: linear-gradient(180deg, rgba(5,5,5,.52), rgba(5,5,5,.18) 52%, transparent 76%)" in html
        assert "backdrop-filter: blur(64px) saturate(1.5)" in html
        assert ".topbar-inner { position: relative; z-index: 50; max-width: 1920px; height: 72px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between;" in html
        assert ".section-nav { display: flex; flex-wrap: nowrap; gap: 32px;" in html
        assert ".nav-pill { position: relative; min-height: 0; height: 12px; border: 0; border-radius: 0; padding: 0; background: transparent;" in html
        assert ".nav-pill.active::after, .nav-pill:hover::after { width: 100%; }" in html
        assert ".top-button { height: 25px; min-height: 25px; border-radius: 3px;" in html
        assert ".dashboard-shell { position: relative; z-index: 1; width: min(1240px, calc(100vw - 48px)); margin: 0 auto; padding: 120px 0 48px;" in html
        assert "#ff6b1a" not in html
        assert "rgba(255,255,255,.98)" not in html
        assert "border-radius: 22px" not in html
        assert "border-radius: 16px" not in html
        assert "border-radius: 999px" not in html
        assert "linear-gradient(135deg, var(--blue), var(--green))" not in html
        assert "#7dd3fc" not in html
        assert "/api/activity?limit=${pageSize}&offset=${activityState.offset}" in html
        assert "/api/usage?bucket=${encodeURIComponent(bucket)}&limit=${usagePageSize}&offset=${usageState.offset}" in html
        assert "/api/memories?limit=${pageSize}&offset=${memoryState.offset}" in html
        assert "/api/history-records?limit=${pageSize}&offset=${historyState.offset}" in html
        assert "const usageState = { offset: 0, count: 0 };" in html
        assert "const usagePageSize = PAGE_SIZE;" in html
        assert "renderPager('usage', usageState, usagePageSize)" in html
        assert "data.series.map(row =>" not in html
        assert "target === 'usage' ? usageState" in html
        assert "target === 'usage' ? loadUsage" in html
        assert "resetStateAndLoad(usageState, loadUsage)" in html
        assert "{label:'Copy'" not in html
        assert 'data-copy-id="${esc(item.memory_id)}"' not in html
        assert ">Copy ID</button>" not in html
        assert "function contentCell" in html
        assert "function toggleContentCell" in html
        assert "data-expandable-content" in html
        assert "aria-expanded=\"false\"" in html
        assert ".content-cell.expanded" in html
        assert ".table-shell.content-expanded .table-wrap" in html
        assert "cell.closest('.table-shell')" in html
        assert "shell.classList.toggle('content-expanded'" in html
        assert "event.key === 'Enter' || event.key === ' '" in html
        assert "contentCell(item.content, 190, 'structured')" in html
        assert "contentCell(item.content, 190, 'history')" in html
        assert "textCell(item.summary, 180)" in html
        assert "function badgeClassFor" in html
        assert "function badgeFor" in html
        assert "function layerBadgeClassFor" in html
        assert "function layerBadgeFor" in html
        assert "layerBadgeFor(item.layer)" in html
        assert ".layer-badge" in html
        assert ".layer-badge-l1" in html
        assert ".layer-badge-l2" in html
        assert ".layer-badge-l3" in html
        assert ".layer-badge-l4" in html
        assert "badgeFor(item.kind)" in html
        assert "badgeFor(item.event)" in html
        assert "beijingTimestamp(item.created_at)" in html
        assert "title=\"${esc(item.created_at)}\">${esc(beijingTimestamp(item.created_at))}</code>" in html
        assert "data-badge-kind" in html
        assert ".badge-kind-add" in html
        assert ".badge-kind-search" in html
        assert ".badge-kind-update" in html
        assert ".badge-kind-delete" in html
        assert ".badge-kind-pipeline" in html
        assert "<span class=\"badge\">${esc(item.kind)}</span>" not in html
        assert "<span class=\"badge\">${esc(item.event)}</span>" not in html

        overview = _get_json(base_url + "/api/overview")
        assert "health" in overview
        assert "totals" in overview

        usage = _get_json(base_url + "/api/usage?bucket=day")
        assert usage["bucket"] == "day"
        assert "series" in usage

        activity = _get_json(base_url + "/api/activity?limit=5")
        assert activity["limit"] == 5
        assert "items" in activity

        memories = _get_json(base_url + "/api/memories?limit=5")
        assert memories["source"] == "vector_db.chroma_active"
        assert memories["limit"] == 5
        assert "items" in memories

        history_records = _get_json(base_url + "/api/history-records?limit=5&layer=l1_raw")
        assert history_records["source"] == "history_db.memory_history"
        assert history_records["limit"] == 5
        assert "items" in history_records

        health = _get_json(base_url + "/api/health")
        assert "history_db" in health
        assert "cache_db" in health
        assert "vector_db" in health
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_rejects_non_get_and_unknown_paths(tmp_path):
    server, thread, base_url = _server_for_tmp_home(tmp_path)
    try:
        request = urllib.request.Request(base_url + "/api/overview", data=b"{}", method="POST")
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("POST /api/overview should return 405")

        try:
            urllib.request.urlopen(base_url + "/api/not-found", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("unknown route should return 404")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
