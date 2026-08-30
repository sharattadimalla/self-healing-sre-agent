from __future__ import annotations

import pytest

from app.clients import DockerOpsClient, DockerOpsError, RunResult


async def test_scale_emits_expected_compose_argv(docker_client, fake_runner):
    await docker_client.scale(4)
    assert fake_runner.calls[-1] == [
        "docker", "compose", "-p", "sre-demo",
        "-f", "/workspace/docker-compose.yml",
        "up", "-d", "--no-recreate", "--no-build", "--scale", "api=4", "api",
    ]


async def test_restart_emits_expected_compose_argv(docker_client, fake_runner):
    await docker_client.restart()
    assert fake_runner.calls[-1] == [
        "docker", "compose", "-p", "sre-demo",
        "-f", "/workspace/docker-compose.yml", "restart", "api",
    ]


async def test_replica_count_parses_jsonlines(docker_client, fake_runner):
    fake_runner.queue.append(
        RunResult(
            0,
            '{"Service":"api","State":"running"}\n'
            '{"Service":"api","State":"running"}\n'
            '{"Service":"api","State":"exited"}\n',
            "",
        )
    )
    assert await docker_client.replica_count() == 2


async def test_replica_count_parses_json_array(docker_client, fake_runner):
    fake_runner.queue.append(
        RunResult(0, '[{"Service":"api","State":"Up 3 minutes"},'
                     '{"Service":"api","State":"Up 3 minutes"}]', "")
    )
    assert await docker_client.replica_count() == 2


async def test_nonzero_exit_raises_dockeropserror(docker_client, fake_runner):
    fake_runner.queue.append(RunResult(1, "", "no such service"))
    with pytest.raises(DockerOpsError):
        await docker_client.restart()
