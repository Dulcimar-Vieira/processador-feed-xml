#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consolida até 1.000 vagas únicas e preserva o arquivo anterior em caso de erro."""

import json
from datetime import datetime, timezone
from pathlib import Path

FOLDER = Path("json_parts")
OUTPUT = Path("merged.json")
MAX_JOBS = 1000


def main():
    parts = sorted(
        FOLDER.glob("part_*.json"),
        key=lambda item: int(item.stem.split("_", 1)[1]),
    )
    if not parts:
        raise SystemExit("ERRO: nenhum part_*.json encontrado; merged.json anterior preservado.")

    jobs = []
    seen = set()
    for part in parts:
        try:
            with part.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"ERRO ao ler {part}: {exc}; merged.json anterior preservado.")
        if not isinstance(data, list):
            raise SystemExit(f"ERRO: {part} não contém uma lista; merged.json anterior preservado.")

        for job in data:
            if not isinstance(job, dict):
                continue
            url = (job.get("url") or "").strip().rstrip("/")
            unique_key = url or job.get("id") or job.get("hash_unico")
            if not unique_key or unique_key in seen:
                continue
            seen.add(unique_key)
            jobs.append(job)
            if len(jobs) >= MAX_JOBS:
                break
        if len(jobs) >= MAX_JOBS:
            break

    if not jobs:
        raise SystemExit("ERRO: nenhum registro válido; merged.json anterior preservado.")

    result = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "total_vagas": len(jobs),
        "vagas": jobs,
    }
    temp = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp.replace(OUTPUT)

    print(f"Arquivos processados: {len(parts)}")
    print(f"Total de vagas únicas no merged.json: {len(jobs)}")
    print(f"Arquivo gerado: {OUTPUT}")


if __name__ == "__main__":
    main()
