import time
import traceback

from app import logger, scheduler, xray
from app.db import GetDB, crud
from app.models.node import NodeStatus
from app.xray.reconnect import default_clock, get_policy
from config import JOB_CORE_HEALTH_CHECK_INTERVAL
from xray_api import exc as xray_exc


def core_health_check(clock=default_clock):
    """The 10 s health-check tick.

    v0.9.0 Task 4: consult the per-node ReconnectPolicy before
    scheduling a reconnect. A node whose backoff cooldown hasn't
    elapsed is skipped THIS tick — the policy spaces attempts across
    ticks instead of hammering every 10 s indefinitely.

    The ``clock`` parameter is injected so tests can pace ticks
    against a fake clock; production uses ``default_clock`` (monotonic).
    """
    config = None

    # main core
    if not xray.core.started:
        if not config:
            config = xray.config.include_db_users()
        xray.core.restart(config)

    now = clock()

    # nodes' core
    for node_id, node in list(xray.nodes.items()):
        # Gate every node on its policy. If a previous attempt failed
        # and the cooldown hasn't elapsed, skip this tick. Operator-
        # initiated reconnect paths (POST /node/{id}/reconnect, lifespan
        # boot, node-add, etc.) do NOT pass through this loop and are
        # not gated — see Task 4 discovery report §5.
        policy = get_policy(node_id)
        if not policy.should_attempt(now):
            continue

        if node.connected:
            try:
                assert node.started
                node.api.get_sys_stats(timeout=2)
            except (ConnectionError, xray_exc.XrayError, AssertionError):
                if not config:
                    config = xray.config.include_db_users()
                xray.operations.restart_node(node_id, config)

        if not node.connected:
            if not config:
                config = xray.config.include_db_users()
            xray.operations.connect_node(node_id, config)


def start_core():
    """Lifespan startup hook (registered by app/__init__.py lifespan).

    Builds the Xray config from active+on_hold users, starts the main
    core, connects each enabled node, and registers the
    core_health_check interval job.
    """
    logger.info("Generating Xray core config")

    start_time = time.time()
    config = xray.config.include_db_users()
    logger.info(f"Xray core config generated in {(time.time() - start_time):.2f} seconds")

    # main core
    logger.info("Starting main Xray core")
    try:
        xray.core.start(config)
    except Exception:
        traceback.print_exc()

    # nodes' core
    logger.info("Starting nodes Xray core")
    with GetDB() as db:
        dbnodes = crud.get_nodes(db=db, enabled=True)
        node_ids = [dbnode.id for dbnode in dbnodes]
        for dbnode in dbnodes:
            crud.update_node_status(db, dbnode, NodeStatus.connecting)

    for node_id in node_ids:
        xray.operations.connect_node(node_id, config)

    scheduler.add_job(core_health_check, 'interval',
                      seconds=JOB_CORE_HEALTH_CHECK_INTERVAL,
                      coalesce=True, max_instances=1)


def stop_core_and_disconnect_nodes():
    """Lifespan shutdown hook (registered by app/__init__.py lifespan).

    Stops the local Xray core and disconnects all node connections.
    Exceptions during node disconnect are swallowed intentionally —
    we're tearing down.
    """
    logger.info("Stopping main Xray core")
    xray.core.stop()

    logger.info("Stopping nodes Xray core")
    for node in list(xray.nodes.values()):
        try:
            node.disconnect()
        except Exception:
            pass
