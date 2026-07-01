import json

from exo.utils.info_gatherer.macmon import MacmonMetrics


def raw_metrics(*, cpu_temperature: float, gpu_temperature: float) -> str:
    return json.dumps(
        {
            "timestamp": "2026-06-30T00:00:00Z",
            "temp": {
                "cpu_temp_avg": cpu_temperature,
                "gpu_temp_avg": gpu_temperature,
            },
            "memory": {
                "ram_total": 64 * 1024**3,
                "ram_usage": 16 * 1024**3,
                "swap_total": 0,
                "swap_usage": 0,
            },
            "ecpu_usage": [1200, 0.1],
            "pcpu_usage": [1300, 0.2],
            "gpu_usage": [0, 0.0],
            "all_power": 10.0,
            "ane_power": 0.0,
            "cpu_power": 2.0,
            "gpu_power": 0.0,
            "gpu_ram_power": 0.0,
            "ram_power": 1.0,
            "sys_power": 7.0,
        }
    )


def test_uses_gpu_temperature_when_sensor_is_active() -> None:
    metrics = MacmonMetrics.from_raw_json(
        raw_metrics(cpu_temperature=45.0, gpu_temperature=42.0)
    )

    assert metrics.system_profile.temp == 42.0


def test_falls_back_to_cpu_temperature_when_gpu_sensor_is_inactive() -> None:
    metrics = MacmonMetrics.from_raw_json(
        raw_metrics(cpu_temperature=45.0, gpu_temperature=2.37)
    )

    assert metrics.system_profile.temp == 45.0
