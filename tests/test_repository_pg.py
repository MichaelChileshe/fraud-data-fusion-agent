"""Repository queries against real PostgreSQL, on a small resolved world."""

import pytest

from fusion.api.repository import PostgresTxnStore, Repository
from fusion.bus import InMemoryBus
from fusion.config import TOPICS
from fusion.consumer import FusionConsumer
from fusion.produce import publish

pytestmark = pytest.mark.integration


@pytest.fixture()
def repo(pg, pg_dsn, small_world):
    from psycopg_pool import ConnectionPool

    data, truth = small_world
    bus = InMemoryBus()
    publish(bus.producer(), data)
    FusionConsumer(bus.consumer("r", list(TOPICS.values())), bus.producer(), pg).run(idle_exit=0, progress_every=0)
    pool = ConnectionPool(pg_dsn, min_size=1, max_size=2, open=True, kwargs={"autocommit": True})
    yield Repository(pool, PostgresTxnStore(pool)), truth
    pool.close()


def test_search_by_name_finds_all_four_controller_accounts(repo):
    r, truth = repo
    hits = r.search("Sipho Dlamini", limit=10)
    found = {h["node_id"].split(":")[1] for h in hits if h["kind"] == "account"}
    assert set(truth["controller_accounts"]) <= found
    assert any(h["kind"] == "sanction" for h in hits)


def test_devices_connect_the_whole_ring(repo):
    r, truth = repo
    mule0 = truth["mule_accounts"][0]
    net = r.network(f"account:{mule0}", depth=2, kinds=["LOGGED_IN_FROM"])
    accounts = {n["node_id"].split(":")[1] for n in net["nodes"] if n["kind"] == "account"}
    dev_map = truth["ring_device_map"]
    expected = {a for a, d in dev_map.items() if set(d) & set(dev_map[mule0])}
    assert accounts == expected
    assert all(e["evidence_ids"] for e in net["edges"])


def test_mules_pass_money_through_and_the_shop_does_not(repo):
    r, truth = repo
    mule = r.txn_summary(truth["mule_accounts"][0], 3650)
    shop = r.txn_summary(truth["decoys"]["spaza_shop_account"], 3650)
    assert 0.8 <= mule["pass_through_ratio"] <= 1.0
    assert shop["pass_through_ratio"] in (0.0, None)
    assert mule["evidence_ids"]


def test_evidence_returns_the_original_record(repo):
    r, truth = repo
    ev = r.evidence(truth["sanctions_hit"]["entry_id"])
    assert ev["source"] == "sanctions" and ev["payload"]["full_name"] == "Sipho Mandla Dlamini"
    assert r.evidence("T9999999") is None


def test_identity_links_survive_truncation(repo):
    r, truth = repo
    mule = truth["mule_accounts"][0]
    net = r.network(f"account:{mule}", per_node=5)  # far more links than fit
    kinds = {e["kind"] for e in net["edges"]}
    assert net["truncated"]
    assert {"OWNS", "REGISTERED_PHONE"} <= kinds
