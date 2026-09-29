import os

import pytest

os.environ.setdefault("OTEL_ENABLED", "false")

import fusion.config  # noqa: E402,F401  (reads .env, which is where PG_TEST_DSN usually lives)


@pytest.fixture(scope="session")
def small_world(tmp_path_factory):
    """A small generated data set (fast) plus its ground truth, written to disk."""
    from datetime import date

    from fusion.generate import Generator, write

    out = tmp_path_factory.mktemp("data")
    world = Generator(customers=400, transactions=3000, seed=7, bad_rate=0.03,
                      dup_rate=0.01, as_of=date.today()).build()
    write(world, out, seed=7)
    return out, world.truth
