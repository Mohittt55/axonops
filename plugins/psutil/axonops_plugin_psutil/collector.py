"""psutil collector — local machine metrics via OS kernel."""
from __future__ import annotations

import socket
from datetime import datetime

import psutil

from axonops_sdk import BaseCollector, Metric, MetricBatch


class PsutilCollector(BaseCollector):
    plugin_name    = "psutil"
    plugin_version = "1.0.0"
    description    = "Local machine metrics: CPU, memory, disk, network, processes"

    async def collect(self) -> MetricBatch:
        host = socket.gethostname()
        now  = datetime.utcnow()
        metrics: list[Metric] = []

        def m(name: str, value: float, unit: str = "", **tags: str) -> Metric:
            return Metric(name=name, value=round(value, 2), unit=unit,
                         timestamp=now, tags=dict(tags))

        # CPU
        cpu_pct      = psutil.cpu_percent(interval=0.1)
        cpu_count    = psutil.cpu_count(logical=True)
        load1, load5, load15 = psutil.getloadavg()
        metrics += [
            m("cpu_percent",    cpu_pct,   "%"),
            m("cpu_count",      cpu_count, "cores"),
            m("load_avg_1m",    load1),
            m("load_avg_5m",    load5),
            m("load_avg_15m",   load15),
        ]

        # Memory
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        metrics += [
            m("mem_total_mb",   vm.total   / 1024**2, "MB"),
            m("mem_used_mb",    vm.used    / 1024**2, "MB"),
            m("mem_available_mb", vm.available / 1024**2, "MB"),
            m("mem_percent",    vm.percent, "%"),
            m("swap_percent",   sw.percent, "%"),
        ]

        # Disk (root partition)
        try:
            disk = psutil.disk_usage("/")
            dio  = psutil.disk_io_counters()
            metrics += [
                m("disk_used_percent", disk.percent, "%"),
                m("disk_free_gb",      disk.free / 1024**3, "GB"),
                m("disk_read_mb",      (dio.read_bytes  if dio else 0) / 1024**2, "MB"),
                m("disk_write_mb",     (dio.write_bytes if dio else 0) / 1024**2, "MB"),
            ]
        except Exception:
            pass

        # Network
        try:
            nio = psutil.net_io_counters()
            metrics += [
                m("net_bytes_sent_mb", nio.bytes_sent / 1024**2, "MB"),
                m("net_bytes_recv_mb", nio.bytes_recv / 1024**2, "MB"),
                m("net_packets_sent",  float(nio.packets_sent)),
                m("net_packets_recv",  float(nio.packets_recv)),
                m("net_errin",         float(nio.errin)),
                m("net_errout",        float(nio.errout)),
            ]
        except Exception:
            pass

        # Processes
        proc_count = len(psutil.pids())
        metrics.append(m("process_count", float(proc_count), "procs"))

        # Top CPU process
        try:
            top = max(psutil.process_iter(["pid", "name", "cpu_percent"]),
                      key=lambda p: p.info["cpu_percent"] or 0)
            metrics.append(m("top_process_cpu", top.info["cpu_percent"] or 0,
                             "%", process=top.info["name"] or "unknown"))
        except Exception:
            pass

        return MetricBatch(
            source=self.plugin_name,
            host=host,
            metrics=metrics,
            metadata={"cpu_count": cpu_count},
        )

    async def health_check(self) -> bool:
        try:
            psutil.cpu_percent(interval=0)
            return True
        except Exception:
            return False
