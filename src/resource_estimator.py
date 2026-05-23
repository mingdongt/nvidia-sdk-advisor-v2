"""Resource estimation and constraint checking for InstallConfigs."""
import json
from pathlib import Path
from src.models import InstallConfig

_MODEL_PATH = Path(__file__).resolve().parents[1] / "data" / "resource_model.json"


def _load_model() -> dict:
    return json.loads(_MODEL_PATH.read_text(encoding="utf-8"))


def estimate_resources(cfg: InstallConfig) -> dict:
    model = _load_model()
    jp = model["jetpack_versions"].get(cfg.version, {})
    host_disk = jp.get("host_disk_gb", 30)
    target_disk = jp.get("target_disk_gb", 15)
    ram = jp.get("os_ram_overhead_gb", 1.5)
    breakdown = [f"JetPack {cfg.version}: target={target_disk}GB host={host_disk}GB"]
    for sdk in cfg.additional_sdks:
        info = model["addon_sdks"].get(sdk, {"disk_gb": 2.0, "runtime_ram_gb": 1.0})
        if info.get("host_only"):
            host_disk += info["disk_gb"]
            breakdown.append(f"{sdk} (host-only): +{info['disk_gb']}GB host")
        else:
            target_disk += info["disk_gb"]
            host_disk += info["disk_gb"]
            ram += info["runtime_ram_gb"]
            breakdown.append(f"{sdk}: +{info['disk_gb']}GB disk, +{info['runtime_ram_gb']}GB RAM")
    return {
        "host_disk_gb": round(host_disk, 1),
        "target_disk_gb": round(target_disk, 1),
        "runtime_ram_gb": round(ram, 1),
        "breakdown": breakdown,
    }


def check_constraints(cfg: InstallConfig, available_disk_gb: float, available_ram_gb: float) -> dict:
    est = estimate_resources(cfg)
    violations = []
    if est["target_disk_gb"] > available_disk_gb:
        violations.append(
            f"target disk insufficient: need {est['target_disk_gb']}GB, have {available_disk_gb}GB"
        )
    if est["runtime_ram_gb"] > available_ram_gb:
        violations.append(
            f"runtime RAM insufficient: need {est['runtime_ram_gb']}GB, have {available_ram_gb}GB"
        )
    return {
        "fits": len(violations) == 0,
        "violations": violations,
        "estimate": est,
    }
